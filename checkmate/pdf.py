"""PDF page renderer -- turns an uploaded PDF into per-page PNG images so the vision Parser
can read it one page at a time. Uses PyMuPDF (fitz). Port of lib/agent/pdf.ts.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import fitz  # pymupdf

from .models import ImageInput

RENDER_DPI = 150  # enough for handwritten-math OCR; keeps the base64 payload small
# Guard against runaway cost/time on an oversized upload. Default 12 keeps a run inside
# Vercel's 300s ceiling; dev harnesses raise it via CHECKMATE_MAX_PAGES to read a booklet.
MAX_PAGES = int(os.environ.get("CHECKMATE_MAX_PAGES") or 12)


@dataclass
class PdfRender:
    images: list[ImageInput]  # one high-detail PNG data URL per rendered page
    page_count: int           # pages in the document
    rendered: int             # pages actually rendered (min(page_count, MAX_PAGES))


def render_pdf_to_images(pdf: bytes) -> PdfRender:
    """Render up to MAX_PAGES of a PDF to PNG images. Raises if the bytes are not a readable
    PDF; the caller turns that into a clean 400."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page_count = doc.page_count
        rendered = min(page_count, MAX_PAGES)
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
