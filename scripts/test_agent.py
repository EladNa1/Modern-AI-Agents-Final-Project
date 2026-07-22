"""Dev harness -- runs the full orchestrator (incl. Reflector loop). Accepts an image or a
PDF (PDF takes the same render path as /api/execute). Port of scripts/test_agent.ts.

Run: python scripts/test_agent.py <path> ["<exam label>"]
"""
from __future__ import annotations

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.models import ImageInput  # noqa: E402
from checkmate.orchestrator import run_agent  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("public", "exam_q3c_scan.png")
    exam = sys.argv[2] if len(sys.argv) > 2 else None
    data = open(path, "rb").read()
    name = os.path.basename(path)
    kb = round(len(data) / 1024)

    if path.lower().endswith(".pdf"):
        render = render_pdf_to_images(data)
        images = render.images
        source_label = (f"{name} ({kb} KB, {render.rendered}/{render.page_count} pages)"
                        if render.rendered < render.page_count
                        else f"{name} ({kb} KB, {render.page_count} pages)")
        print(f"rendered {render.rendered}/{render.page_count} PDF pages")
    else:
        ext = "png" if path.lower().endswith(".png") else "jpeg"
        images = [ImageInput(data_url=f"data:image/{ext};base64,{base64.b64encode(data).decode('ascii')}", detail="high")]
        source_label = f"{name} ({kb} KB)"

    t0 = time.time()
    result = run_agent(images, instructions="", source_label=source_label, exam=exam)
    print(f"elapsed: {time.time() - t0:.1f}s")
    print("status:", result["status"], "| error:", result.get("error"))
    print("\n=== RESPONSE ===\n" + (result.get("response") or ""))
    print("\n=== STEPS (order + module) ===")
    for i, s in enumerate(result["steps"]):
        print(f"  {i + 1}. {s['module']}" + (f" · {s['pattern']}" if s.get("pattern") else ""))


if __name__ == "__main__":
    main()
