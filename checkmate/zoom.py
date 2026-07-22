"""Zoom re-read tool -- Pass 2 of the grader's READING POLICY.

Handwriting is often messy; before declaring a step illegible the pipeline may re-read a
specific region at higher resolution. Two ways to get that region, both returning an
ImageInput the vision model can read:

- `crop_region(page_image, bbox, scale)`  -- crop an already-rendered page PNG and upscale
  it (Pillow). Matches the brief's `zoom_read(page_image, bbox)` signature.
- `rerender_pdf_region(pdf, page, bbox, dpi)` -- re-render the region straight from the
  source PDF at high DPI (PyMuPDF `clip=`). Sharper than upscaling a raster; use when the
  caller still holds the PDF bytes.

`zoom_read` crops + transcribes that one region, and `ZoomBudget` enforces the policy cap
of at most two zoom reads per question.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import fitz  # pymupdf -- already a dependency (see pdf.py)

from .llm import StepLog, chat
from .models import ImageInput, Usage

# Pass-2 default upscale. 2.5x sits in the brief's "2-3x" band: enough to resolve exponents
# and sub/superscripts without ballooning the base64 payload.
DEFAULT_SCALE = 2.5
DEFAULT_RERENDER_DPI = 300  # vs pdf.RENDER_DPI=150 -> ~2x sharper straight from the source

ZOOM_TRANSCRIBE_SYSTEM = (
    "You are the zoom re-read helper of CheckMate's vision Parser. You are given a CROPPED, "
    "magnified region of one handwritten Technion Calculus 1 exam page. Transcribe ONLY what "
    "is inside this crop, faithfully — preserve Hebrew and mathematics exactly, do not grade "
    "or solve, do not invent text that is cut off at an edge. Re-express math in LaTeX where "
    "it helps. If a symbol is still unreadable at this magnification, write [illegible].\n\n"
    'Return ONLY a JSON object: {"text":"...","latex":"...","confidence":0.0}'
)


@dataclass
class ZoomResult:
    text: str            # fresh transcription of the cropped region
    crop: ImageInput     # the magnified crop actually read (for the trace)
    raw: str             # model's raw reply
    usage: Usage


@dataclass
class ZoomBudget:
    """Caps zoom reads at `limit` per question (READING POLICY: up to 2). One instance per
    question; `spend()` returns False once the budget is exhausted so the caller stops."""
    limit: int = 2
    used: int = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def spend(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Split a `data:image/png;base64,....` URL into (raw bytes, mime subtype)."""
    m = re.match(r"data:image/(?P<sub>[a-zA-Z0-9.+-]+);base64,(?P<b64>.*)$", data_url, re.DOTALL)
    if not m:
        raise ValueError("zoom: page_image is not a base64 image data URL")
    return base64.b64decode(m.group("b64")), m.group("sub").lower()


def _norm_bbox(bbox, width: int, height: int) -> tuple[int, int, int, int]:
    """Normalize a bbox to integer pixel (x0,y0,x1,y1), clamped to the image and ordered.

    Accepts either normalized coords in [0,1] (preferred — vision models emit fractions) or
    absolute pixels (any coordinate > 1 is treated as already-pixel). Raises on a degenerate
    (zero-area) box."""
    if bbox is None or len(bbox) != 4:
        raise ValueError("zoom: bbox must be [x0,y0,x1,y1]")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    normalized = max(x0, y0, x1, y1) <= 1.0
    if normalized:
        x0, x1 = x0 * width, x1 * width
        y0, y1 = y0 * height, y1 * height
    # Order and clamp.
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    ix0, iy0 = max(0, int(x0)), max(0, int(y0))
    ix1, iy1 = min(width, int(round(x1))), min(height, int(round(y1)))
    if ix1 <= ix0 or iy1 <= iy0:
        raise ValueError("zoom: bbox has zero area after clamping to the image")
    return ix0, iy0, ix1, iy1


def crop_region(page_image: ImageInput, bbox, scale: float = DEFAULT_SCALE) -> ImageInput:
    """Crop `page_image` (a rendered-page PNG/JPEG data URL) to `bbox` and upscale by `scale`.
    Returns a fresh high-detail ImageInput. Uses PyMuPDF only, so no extra dependency.

    Works in TRUE pixels: `fitz.Pixmap(bytes).width/height` is the real resolution (unlike a
    page rect, which is in points and skewed by the image's embedded DPI). The crop is taken
    at native resolution, then the magnification is exact — the crop is re-opened with its DPI
    forced to 72 (so 1 point == 1 pixel) and rendered at `matrix=scale`."""
    raw, _sub = _decode_data_url(page_image.data_url)
    pm = fitz.Pixmap(raw)  # true-pixel raster
    x0, y0, x1, y1 = _norm_bbox(bbox, pm.width, pm.height)
    cropped = fitz.Pixmap(pm, pm.width, pm.height, fitz.IRect(x0, y0, x1, y1))

    scale = max(1.0, min(4.0, float(scale)))  # keep the payload sane
    if scale != 1.0:
        cropped.set_dpi(72, 72)
        doc = fitz.open(stream=cropped.tobytes("png"), filetype="png")
        try:
            out = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(scale, scale),
                                              colorspace=fitz.csRGB, alpha=False)
        finally:
            doc.close()
    else:
        out = cropped

    b64 = base64.b64encode(out.tobytes("png")).decode("ascii")
    return ImageInput(data_url=f"data:image/png;base64,{b64}", detail="high")


def rerender_pdf_region(pdf: bytes, page_index: int, bbox,
                        dpi: int = DEFAULT_RERENDER_DPI) -> ImageInput:
    """Re-render one region of a PDF page straight from the source at `dpi` (sharper than
    upscaling a raster). `bbox` is normalized [0,1] or absolute PDF points."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page = doc.load_page(page_index)
        r = page.rect
        x0, y0, x1, y1 = bbox
        if max(bbox) <= 1.0:  # normalized -> PDF points
            x0, x1 = r.x0 + x0 * r.width, r.x0 + x1 * r.width
            y0, y1 = r.y0 + y0 * r.height, r.y0 + y1 * r.height
        clip = fitz.Rect(x0, y0, x1, y1) & r
        if clip.is_empty:
            raise ValueError("zoom: bbox does not intersect the page")
        m = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=m, clip=clip, colorspace=fitz.csRGB, alpha=False)
        b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        return ImageInput(data_url=f"data:image/png;base64,{b64}", detail="high")
    finally:
        doc.close()


def zoom_read(page_image: ImageInput, bbox, log: StepLog | None = None,
              budget: ZoomBudget | None = None, scale: float = DEFAULT_SCALE,
              hint: str = "") -> ZoomResult | None:
    """READING POLICY Pass 2: crop `page_image` to `bbox`, magnify it, and fetch a fresh
    transcription of just that region. Returns None if the per-question zoom budget is spent.

    `hint` (optional) tells the model what to look for, e.g. "read the exponent on (c+9)"."""
    if budget is not None and not budget.spend():
        return None

    crop = crop_region(page_image, bbox, scale)
    user = ("Transcribe this magnified crop of a handwritten exam. Return only the JSON object."
            + (f"\nFocus: {hint}" if hint else ""))
    text, usage = chat(ZOOM_TRANSCRIBE_SYSTEM, user, images=[crop],
                       max_tokens=800, json_mode=True)

    if log is not None:
        log.add("Zoom", ZOOM_TRANSCRIBE_SYSTEM, user,
                {"bbox": list(bbox), "scale": scale, "reply": text}, "Vision OCR (zoom)", usage)

    return ZoomResult(text=text, crop=crop, raw=text, usage=usage)
