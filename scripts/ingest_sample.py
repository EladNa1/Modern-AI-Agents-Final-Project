"""Ingest a booklet PDF as a bundled sample: parse it ONCE with the (parallel) vision
Parser, save the cached transcription to checkmate/kb/samples/, and render compressed page
previews to static/samples/<sample_dir>/ for the GUI.

Run:  python scripts/ingest_sample.py <pdf_path> <sample_json_name> <sample_dir>
e.g.  python scripts/ingest_sample.py "Data/מבחן1/104041_2024_Winter_A_93.pdf" 104041-2024W-A-93.json sample2
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkmate.llm import StepLog  # noqa: E402
from checkmate.parser import run_parser  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    pdf_path, json_name, sample_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    data = open(os.path.join(ROOT, pdf_path), "rb").read()

    # 1. Page previews for the GUI (cheap JPEGs, lazy-loaded).
    prev_dir = os.path.join(ROOT, "static", "samples", sample_dir)
    os.makedirs(prev_dir, exist_ok=True)
    doc = fitz.open(stream=data, filetype="pdf")
    for i, page in enumerate(doc):
        page.get_pixmap(dpi=80).save(os.path.join(prev_dir, f"page-{i + 1:02d}.jpg"),
                                     jpg_quality=55)
    print(f"previews: {doc.page_count} pages -> {prev_dir}")

    # 2. One-time vision parse (the only vision cost this booklet will ever incur).
    render = render_pdf_to_images(data, max_pages=60)
    log = StepLog()
    t0 = time.time()
    parsed = run_parser(render.images, log)
    dt = time.time() - t0
    cost = log.cost_by_stage()
    print(f"parsed {render.page_count} pages in {dt:.0f}s "
          f"(parallel) — {len(parsed.fragments)} fragments, est ${cost['total']:.4f}")

    # 3. Save the cached transcription in the bundled-samples format.
    out = os.path.join(ROOT, "checkmate", "kb", "samples", json_name)
    json.dump({"parser_version": "bundled", "exam_meta": parsed.exam_meta, "raw": parsed.raw,
               "fragments": [f.__dict__ for f in parsed.fragments]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", out)
    print("exam_meta:", parsed.exam_meta)
    print("pages:", doc.page_count)


if __name__ == "__main__":
    main()
