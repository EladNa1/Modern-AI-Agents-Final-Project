"""PDF page renderer -- turns an uploaded PDF into per-page PNG images so the vision Parser
can read it one page at a time. Uses PyMuPDF (fitz). Port of lib/agent/pdf.ts.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import fitz  # pymupdf

from .config import CONFIG
from .models import ImageInput

RENDER_DPI = 150  # enough for handwritten-math OCR; keeps the base64 payload small
# Page cap lives in the config (CONFIG.render_max_pages, env CHECKMATE_MAX_PAGES). Default 12
# keeps a run inside Vercel's 300s ceiling; an 18-page booklet needs it raised or its later
# sections (T/F, MC) never reach the parser.


@dataclass
class PdfRender:
    images: list[ImageInput]  # one high-detail PNG data URL per rendered page
    page_count: int           # pages in the document
    rendered: int             # pages actually rendered (min(page_count, MAX_PAGES))


def render_pdf_to_images(pdf: bytes, max_pages: int | None = None) -> PdfRender:
    """Render up to `max_pages` (default CONFIG.render_max_pages) of a PDF to PNG images.
    Raises if the bytes are not a readable PDF; the caller turns that into a clean 400."""
    cap = max_pages if max_pages is not None else CONFIG.render_max_pages
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page_count = doc.page_count
        rendered = min(page_count, cap)
        scale = RENDER_DPI / 72  # PDF user space is 72 dpi
        matrix = fitz.Matrix(scale, scale)

        images: list[ImageInput] = []
        for i in range(rendered):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
            images.append(ImageInput(data_url=f"data:image/png;base64,{b64}", detail="high"))
        return PdfRender(images=images, page_count=page_count, rendered=rendered)
    finally:
        doc.close()
