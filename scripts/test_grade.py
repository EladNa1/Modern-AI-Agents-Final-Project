"""Dev harness -- full chain Parser -> Retriever -> Grader on one scan.
Port of scripts/test_grade.ts.

Run: python scripts/test_grade.py <imagePath> ["<exam label>"]
"""
from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.grader import run_grader  # noqa: E402
from checkmate.llm import StepLog  # noqa: E402
from checkmate.models import ImageInput  # noqa: E402
from checkmate.parser import run_parser  # noqa: E402
from checkmate.retriever import retrieve  # noqa: E402


def main() -> None:
    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("public", "exam_q3c_scan.png")
    exam = sys.argv[2] if len(sys.argv) > 2 else None

    data = open(img_path, "rb").read()
    ext = "png" if img_path.lower().endswith(".png") else "jpeg"
    data_url = f"data:image/{ext};base64,{base64.b64encode(data).decode('ascii')}"

    log = StepLog()
    parsed = run_parser([ImageInput(data_url=data_url, detail="high")], log)
    print(f"Parsed {len(parsed.fragments)} question(s).")
    if exam:
        print(f"Scoped to exam: {exam}")

    for q in parsed.fragments:
        retrieved = retrieve(q.id, f"{q.text} {q.latex or ''}", log, exam)
        label = f"{retrieved.entry.id} ({retrieved.entry.points} pts)" if retrieved else "NONE"
        print(f"\nRetrieved for {q.id}: {label}")
        grade = run_grader(q, retrieved, log)
        print(f"=== GRADE {grade.id} ===")
        print(f"score: {grade.score}/{grade.max}  status: {grade.status}  confidence: {grade.confidence}")
        print(f"feedback: {grade.feedback}")

    print("\n=== USAGE (tokens) ===", log.usage)


if __name__ == "__main__":
    main()
