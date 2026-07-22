"""Central registry of the bundled official-solution exams. Port of lib/kb/exams.ts.

Question ids AND topics repeat across exams (every exam has a "Q1"; several share near-
identical questions such as "state Rolle's theorem"), so retrieval that is not scoped to
one exam can ground a grade on the wrong exam's question. The teacher tells CheckMate which
exam a scan belongs to; the Retriever then filters to it. This module is the single source
of that exam list, loaded from the bundled solution JSONs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..models import SolutionEntry

_KB_DIR = os.path.dirname(os.path.abspath(__file__))
_SOL_DIR = os.path.join(_KB_DIR, "solutions")

# Add one entry per newly ingested exam.
_FILES = [
    "2023w_final_A.json",
    "2024w_final_A.json",
    "2025w_final_A.json",
    "2025s_final_A.json",
    "2026w_final_B.json",
]


@dataclass
class ExamKB:
    exam: str    # e.g. "2025s final A" -- matches the `exam` field on every Pinecone vector
    course: str  # e.g. "104018"
    questions: dict[str, SolutionEntry]


def _entry(e: dict) -> SolutionEntry:
    return SolutionEntry(
        id=e["id"], points=e.get("points", 0), problem=e.get("problem", ""),
        official_solution=e.get("official_solution", ""), topic=e.get("topic"),
        final_answer=e.get("final_answer"), notes=e.get("notes"),
    )


def _load(fname: str) -> ExamKB:
    with open(os.path.join(_SOL_DIR, fname), encoding="utf-8") as f:
        data = json.load(f)
    questions = {k: _entry(e) for k, e in data.get("questions", {}).items()}
    return ExamKB(exam=data.get("exam", ""), course=data.get("course", ""), questions=questions)


EXAMS: list[ExamKB] = [_load(f) for f in _FILES]


def exam_options() -> list[dict]:
    """Compact list for the UI picker and /api/exams. `value` is the exam label stored in
    Pinecone metadata (what the Retriever filters on); `label` is what the teacher reads."""
    return [{"value": k.exam, "course": k.course, "label": f"{k.exam} · {k.course}"} for k in EXAMS]
