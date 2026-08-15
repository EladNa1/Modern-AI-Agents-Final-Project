"""Focused mark-verification pass for a bundled sample's MC page: crop each question band
at high DPI and ask the vision model ONLY which option is marked (scribbled = cancelled).
Much more reliable than reading the whole crowded page in one call. Patches the bundled
transcription's "מסומן:" lines in place.

Run: python scripts/verify_marks.py <pdf_path> <sample_json_name> <page_number>
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkmate.llm import chat, extract_json  # noqa: E402
from checkmate.models import ImageInput  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SYSTEM = (
    "You verify a student's mark on ONE multiple-choice exam item (Hebrew, RTL). "
    "You get a cropped image showing one question and its options. Rules: "
    "a SCRIBBLED-OUT/blacked-over mark is a CANCELLED answer, NOT the choice; the final "
    "answer is the clean circle/mark. Match the circle to the letter it actually surrounds. "
    "Ignore red-pen grader marks. Reply ONLY JSON: "
    '{"question": <question number printed in the header, or null>, '
    '"marked": "<letter א-ה, or אין if none, or לא ברור if ambiguous>", '
    '"cancelled": "<letter that was scribbled out, or null>"}')


def main() -> None:
    pdf_path, json_name, page_no = sys.argv[1], sys.argv[2], int(sys.argv[3])
    doc = fitz.open(os.path.join(ROOT, pdf_path))
    page = doc[page_no - 1]
    pix = page.get_pixmap(dpi=200)
    png = pix.tobytes("png")
    img = fitz.Pixmap(png)  # noqa: F841  (full page kept for reference)

    # Overlapping horizontal bands -- each contains at least one full question block.
    bands = [(0.16, 0.40), (0.34, 0.58), (0.52, 0.70), (0.62, 0.84), (0.78, 0.97)]
    results: dict[int, dict] = {}
    for y0, y1 in bands:
        clip = fitz.Rect(0, y0 * page.rect.height, page.rect.width, y1 * page.rect.height)
        b64 = base64.b64encode(page.get_pixmap(dpi=200, clip=clip).tobytes("png")).decode()
        image = ImageInput(data_url=f"data:image/png;base64,{b64}", detail="high")
        text, _ = chat(SYSTEM, "Which option is the student's FINAL marked answer in this item?",
                       images=[image], max_tokens=150, json_mode=True)
        d = extract_json(text) or {}
        qn = d.get("question")
        if isinstance(qn, int) and d.get("marked"):
            results.setdefault(qn, d)  # first (most complete) band containing the question wins
        print(f"band {y0}-{y1}: {d}")

    path = os.path.join(ROOT, "checkmate", "kb", "samples", json_name)
    bundle = json.load(open(path, encoding="utf-8"))
    patched = 0
    for f in bundle["fragments"]:
        if f["page"] != page_no:
            continue
        m = re.search(r"שאלה\s*(\d)", f["text"] or "")
        qn = int(m.group(1)) if m else None
        if qn in results:
            new_mark = results[qn]["marked"]
            cancelled = results[qn].get("cancelled")
            suffix = f"מסומן: {new_mark}" + (f" (תשובה קודמת {cancelled} נמחקה)" if cancelled else "")
            new_text, n = re.subn(r"מסומן:[^\n]*", suffix, f["text"])
            if n and new_text != f["text"]:
                f["text"] = new_text
                patched += 1
                print(f"patched Q{qn}: {suffix}")
    json.dump(bundle, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"{patched} fragment(s) patched -> {path}")


if __name__ == "__main__":
    main()
