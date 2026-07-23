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
import re
from dataclasses import dataclass

from ..models import SolutionEntry

_KB_DIR = os.path.dirname(os.path.abspath(__file__))
_SOL_DIR = os.path.join(_KB_DIR, "solutions")

# Add one entry per newly ingested exam.
_FILES = [
    "2023w_final_A.json",
    "2024w_final_A.json",
    "2024w_104041_A.json",
    "2025w_final_A.json",
    "2025s_final_A.json",
    "2026w_final_B.json",
]


@dataclass
class ExamKB:
    exam: str    # e.g. "2025s final A" -- matches the `exam` field on every Pinecone vector
    course: str  # e.g. "104018"
    questions: dict[str, SolutionEntry]

    # Identity parsed from the exam label, for auto-matching a scan to its exam.
    @property
    def year(self) -> str:
        m = re.search(r"(\d{4})", self.exam)
        return m.group(1) if m else ""

    @property
    def term(self) -> str:  # 'w' winter / 's' spring, as written after the year
        m = re.search(r"\d{4}\s*([wsWS])", self.exam)
        return m.group(1).lower() if m else ""

    @property
    def moed(self) -> str:  # 'A' / 'B'
        m = re.search(r"\b([ABab])\b", self.exam)
        return m.group(1).upper() if m else ""


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


def manifest_for(exam_label: str) -> dict | None:
    """Structure the KB knows for an exam: question ids + point values + expected total.
    The orchestrator uses it to decide completion deterministically (8.3) -- never asking the
    model whether a booklet is finished, and never reporting a total built on missing slots."""
    kb = next((k for k in EXAMS if k.exam == exam_label), None)
    if not kb:
        return None
    questions = [{"id": e.id, "max": e.points} for e in kb.questions.values()]
    return {"questions": questions, "expected_total": sum(q["max"] for q in questions)}


def match_exam(course: str | None = None, year: str | None = None,
               moed: str | None = None, term: str | None = None) -> str | None:
    """Map exam-identity fields read off a scan (course number, year, מועד, term) to a KB
    exam label. Returns the label ONLY when the fields single out exactly one exam; 0 or >1
    matches -> None, so the caller falls back to unscoped rather than grounding on a guess.
    `course` is the strongest key (and the most reliable to OCR)."""
    def _digits(s) -> str:
        return re.sub(r"\D", "", str(s or ""))

    cands = list(EXAMS)
    if course and _digits(course):
        c = _digits(course)
        cands = [k for k in cands if _digits(k.course) == c]
    if year and re.search(r"\d{4}", str(year)):
        y = re.search(r"\d{4}", str(year)).group(0)
        cands = [k for k in cands if k.year == y]
    if moed and str(moed).strip():
        mo = str(moed).strip().upper()[:1]
        cands = [k for k in cands if k.moed == mo]
    if term and str(term).strip():
        t = str(term).strip().lower()[:1]
        cands = [k for k in cands if k.term == t]
    return cands[0].exam if len(cands) == 1 else None
