"""Re-read ONE page of a bundled sample booklet with the vision Parser and splice the fresh
fragments into the cached transcription — for repairing a page the original parse read
incompletely (e.g. a missed circled answer). One vision call.

Run: python scripts/reread_sample_page.py <pdf_path> <sample_json_name> <page_number>
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.llm import StepLog  # noqa: E402
from checkmate.parser import run_parser  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    pdf_path, json_name, page_no = sys.argv[1], sys.argv[2], int(sys.argv[3])
    data = open(os.path.join(ROOT, pdf_path), "rb").read()
    render = render_pdf_to_images(data, max_pages=60)
    image = render.images[page_no - 1]

    log = StepLog()
    parsed = run_parser([image], log)
    print(f"re-read page {page_no}: {len(parsed.fragments)} fragments, "
          f"est ${log.cost_by_stage()['total']:.4f}")
    for f in parsed.fragments:
        f.page = page_no  # run_parser numbered the single image as page 1
        print(f"  {f.id:10} conf {f.confidence}: {(f.text or '')[:70].strip()!r}")

    path = os.path.join(ROOT, "checkmate", "kb", "samples", json_name)
    d = json.load(open(path, encoding="utf-8"))
    kept = [f for f in d["fragments"] if f["page"] != page_no]
    removed = len(d["fragments"]) - len(kept)
    d["fragments"] = kept + [f.__dict__ for f in parsed.fragments]
    d["fragments"].sort(key=lambda f: f["page"])
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"spliced: removed {removed} old fragment(s) of page {page_no}, "
          f"added {len(parsed.fragments)}; wrote {path}")


if __name__ == "__main__":
    main()
