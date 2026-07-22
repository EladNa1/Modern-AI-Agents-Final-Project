"""Dev harness -- ingest a full official-solution PDF into a KB JSON file.
Port of scripts/ingest_exam.ts.

Renders every page, extracts questions PER PAGE via a structured vision call, and merges
parts across pages. Question ids and point values come from the PDF text layer
(extract_exam_structure), never the vision model. Output feeds scripts/index_kb.py.

Run: python scripts/ingest_exam.py "<solution.pdf>" <course> "<exam label>" <outSlug>
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.kb.exam_structure import extract_exam_structure  # noqa: E402
from checkmate.llm import chat, extract_json  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402

SYSTEM = """You are building a grading knowledge base for a Technion Calculus exam.
You receive ONE page of the official SOLUTION document (it restates each question and gives the official answer/worked solution).
Extract every exam question or sub-part visible on this page. Do NOT invent questions not shown.

CRITICAL — multiple-choice answer options are NOT sub-parts.
In a multiple-choice question, the Hebrew letters (א)(ב)(ג)(ד) introduce the CANDIDATE ANSWERS to choose between. They are one single question, not four.
Emit exactly ONE entry for such a question (id "Q1", never "Q1a"/"Q1b"/…), list the options inside "problem", and put the correct option in "final_answer".
Only treat a Hebrew letter as a real sub-part when it states its own task to perform — it starts with an instruction verb such as חשבו / חשב / הוכיחו / הוכח / מצאו / מצא / נסחו / קבעו, and usually carries its own point value.
If you are unsure, ask: does this letter give something to SOLVE (sub-part) or something to PICK (option)? Picking means it is an option.

POINTS are supplied with the id list — copy the value given for that id. Do not compute or guess one.

QUESTION IDS ARE GIVEN TO YOU.
The user message lists every question id on this exam, read directly from the document. Use ONLY ids from that list — never invent one, never renumber, never restart at 1.
A long question runs over several pages and the later pages often show only a bare sub-part letter, e.g. "(ב) (10 נקודות) חשבו…". Match it to the right id from the list using the surrounding content.
If a page shows something that matches no id on the list, leave it out.

For each question output:
- id: "Q<number><part>" using the question number and, for a REAL sub-part only, the Hebrew letter mapped to a/b/c/d/e (e.g. שאלה 3 סעיף ג -> "Q3c"; a question with no sub-parts -> "Q3").
- points: integer as described above, else 0.
- topic: a short English topic label (e.g. "improper limit via FTC + L'Hopital").
- problem: the full question statement, Hebrew preserved, math in LaTeX. For multiple choice, include all the options.
- official_solution: the official worked solution / for a true-false or multiple-choice item, state which option is correct and why. Hebrew + LaTeX.
- final_answer: the final result or the correct option (short).
- notes: one key subtlety or common mistake, or "".

Output ONLY JSON, no prose, no code fences:
{"questions":{"Q3c":{"id":"Q3c","points":9,"topic":"...","problem":"...","official_solution":"...","final_answer":"...","notes":"..."}}}"""

_HE = {"א": "a", "ב": "b", "ג": "c", "ד": "d", "ה": "e"}


def norm_id(s: str) -> str:
    o = (s or "").lower()
    for h, e in _HE.items():
        o = o.replace(h, e)
    o = re.sub(r"[^a-z0-9]", "", o)
    return o[1:] if o.startswith("q") else o


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 4:
        print('usage: ingest_exam.py "<solution.pdf>" <course> "<exam label>" <outSlug>')
        sys.exit(1)
    pdf_path, course, exam_label, out_slug = args[0], args[1], args[2], args[3]
    data = open(pdf_path, "rb").read()

    # Structure pass first: ids and point values come from the PDF's own text layer.
    structure = extract_exam_structure(data)
    skeleton = {norm_id(q.id): q for q in structure.questions}
    if not skeleton:
        print("WARNING — no text layer structure found; falling back to vision-extracted ids/points.")
    else:
        marks = ", ".join(f"{q.id}={q.points}" for q in structure.questions)
        ok = " ✓" if structure.total == 100 else "  ← expected 100"
        print(f"structure: {marks}  → total {structure.total}{ok}")
    id_list = ", ".join(f"{q.id} ({q.points} pts)" for q in structure.questions)

    render = render_pdf_to_images(data)
    print(f"{exam_label}: {render.page_count} pages, extracting {render.rendered}…")

    merged: dict[str, dict] = {}
    usage = {"prompt": 0, "completion": 0, "total": 0}

    for p, image in enumerate(render.images):
        on_page = structure.ids_by_page.get(p + 1, [])
        if id_list:
            context = f"The questions on this exam are: {id_list}. Use only these ids."
            context += (f" This page shows: {', '.join(on_page)} — label what you extract with exactly those ids."
                        if on_page else
                        " This page shows no question marker; if it continues a previous question, return nothing.")
        else:
            context = "No id list is available for this exam — infer ids from the page."

        text, u = chat(
            system=SYSTEM,
            user=f"Extract the questions on this solution page (page {p + 1} of {render.rendered}). {context} Return only the JSON object.",
            images=[image], max_tokens=3000, json_mode=True,
        )
        for k in usage:
            usage[k] += u[k]
        page_qs = (extract_json(text) or {}).get("questions", {}) or {}

        skipped = 0
        for e in page_qs.values():
            if not e or not e.get("id") or not (e.get("problem") or e.get("official_solution")):
                continue
            key = norm_id(e["id"])
            known = skeleton.get(key)
            if skeleton and not known:
                skipped += 1
                continue
            points = known.points if known else (e.get("points") or 0)
            ex = merged.get(key)
            if not ex:
                merged[key] = {
                    "id": known.id if known else e["id"], "points": points, "topic": e.get("topic", ""),
                    "problem": e.get("problem", ""), "official_solution": e.get("official_solution", ""),
                    "final_answer": e.get("final_answer", ""), "notes": e.get("notes", ""),
                }
            else:  # continuation on a later page
                ex["problem"] = "\n".join(x for x in [ex["problem"], e.get("problem", "")] if x)
                ex["official_solution"] = "\n".join(x for x in [ex["official_solution"], e.get("official_solution", "")] if x)
                if not ex["final_answer"] and e.get("final_answer"):
                    ex["final_answer"] = e["final_answer"]
                if not ex["topic"] and e.get("topic"):
                    ex["topic"] = e["topic"]
        note = f" (−{skipped} off-skeleton)" if skipped else ""
        print(f"  page {p + 1}: +{len(page_qs)}{note} → {len(merged)} total")

    missing = [q.id for k, q in skeleton.items() if k not in merged]
    zeroed = [e["id"] for e in merged.values() if not e["points"]]
    total = sum(e["points"] for e in merged.values())

    out = {"exam": exam_label, "course": course, "questions": merged}
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checkmate", "kb", "solutions")
    out_file = os.path.join(out_dir, f"{out_slug}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\nwrote {out_file} — {len(merged)} questions")
    print("ids:", ", ".join(f"{e['id']}({e['points']})" for e in merged.values()))
    print(f"TOTAL POINTS: {total}" + (" ✓" if total == 100 else "  ← expected 100, review the ingest"))
    if zeroed:
        print(f"WARNING — {len(zeroed)} question(s) with 0 points: {', '.join(zeroed)}")
    if missing:
        print(f"WARNING — {len(missing)} question(s) in the exam but not extracted: {', '.join(missing)}")
    print("tokens:", usage)


if __name__ == "__main__":
    main()
