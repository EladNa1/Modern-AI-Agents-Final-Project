"""Deterministic ink detection -- zero LLM.

Two jobs, one detector:
  1. Eval metric: how much ink is on the page and where (the "region contains ink" side of
     the false-zero metric -- a region with ink but an empty transcript is a parser miss).
  2. Task 2.4 guard (later): the same per-region ink test decides whether an empty
     transcription must be re-read/escalated rather than scored 0.

Grader ink is RED/pink; student ink is black/blue. We separate them by hue so grader marks
never count as student work (and so red-pen scores can be located for ground-truth reads).
Pure PyMuPDF + a strided scan over the pixmap -- no numpy/opencv, Vercel-safe.
"""
from __future__ import annotations

import fitz  # pymupdf


def classify_pixel(r: int, g: int, b: int) -> str:
    """bg (near-white) | red (grader ink) | student (dark/blue)."""
    if r > 225 and g > 225 and b > 225:
        return "bg"
    if r > 110 and (r - g) > 40 and (r - b) > 40:  # red dominates -> grader pen
        return "red"
    return "student"


def page_ink_stats(page: "fitz.Page", dpi: int = 120, grid: tuple[int, int] = (12, 16),
                   cell_ink_frac: float = 0.02, stride: int = 3) -> dict:
    """Ink statistics for one page. `grid` = (cols, rows) cells; a cell counts as "inked"
    when >= `cell_ink_frac` of its sampled pixels are non-background. `stride` subsamples
    pixels for speed (offline eval, not per-request)."""
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    w, h, n, data = pix.width, pix.height, pix.n, pix.samples
    cols, rows = grid
    cw, ch = w / cols, h / rows
    cell_tot = [[0] * cols for _ in range(rows)]
    cell_ink = [[0] * cols for _ in range(rows)]
    red = student = inked = total = 0

    for y in range(0, h, stride):
        base = y * w * n
        cy = min(rows - 1, int(y / ch))
        for x in range(0, w, stride):
            i = base + x * n
            kind = classify_pixel(data[i], data[i + 1], data[i + 2])
            total += 1
            cx = min(cols - 1, int(x / cw))
            cell_tot[cy][cx] += 1
            if kind != "bg":
                inked += 1
                cell_ink[cy][cx] += 1
                if kind == "red":
                    red += 1
                else:
                    student += 1

    inked_cells = sum(
        1 for ry in range(rows) for rx in range(cols)
        if cell_tot[ry][rx] and cell_ink[ry][rx] / cell_tot[ry][rx] >= cell_ink_frac)
    student_cells = sum(
        1 for ry in range(rows) for rx in range(cols)
        if cell_tot[ry][rx] and (cell_ink[ry][rx] / cell_tot[ry][rx] >= cell_ink_frac))
    return {
        "w": w, "h": h,
        "inked_frac": inked / total if total else 0.0,
        "student_frac": student / total if total else 0.0,
        "red_frac": red / total if total else 0.0,
        "inked_cells": inked_cells, "grid_cells": rows * cols,
        "has_red": red > 0,
    }


def region_has_ink(page: "fitz.Page", bbox, dpi: int = 120,
                   frac_threshold: float = 0.004, stride: int = 2) -> bool:
    """Task-2.4 primitive: does a normalized [x0,y0,x1,y1] region hold meaningful STUDENT
    ink? Used to reject a confident 0 on a region the parser transcribed as empty."""
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    w, h, n, data = pix.width, pix.height, pix.n, pix.samples
    x0, y0, x1, y1 = bbox
    if max(bbox) <= 1.0:
        x0, x1, y0, y1 = x0 * w, x1 * w, y0 * h, y1 * h
    x0, x1 = sorted((max(0, int(x0)), min(w, int(x1))))
    y0, y1 = sorted((max(0, int(y0)), min(h, int(y1))))
    student = total = 0
    for y in range(y0, y1, stride):
        base = y * w * n
        for x in range(x0, x1, stride):
            i = base + x * n
            total += 1
            if classify_pixel(data[i], data[i + 1], data[i + 2]) == "student":
                student += 1
    return total > 0 and (student / total) >= frac_threshold


def booklet_ink_stats(pdf: bytes, max_pages: int = 20) -> list[dict]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        return [page_ink_stats(doc.load_page(i))
                for i in range(min(doc.page_count, max_pages))]
    finally:
        doc.close()
