"""Retriever module -- the RAG stage. Grounds each question in the knowledge base:
returns the official solution, point value, and notes for the matched exam question.
Port of lib/agent/retriever.ts.

Two layers:
  1. semantic Pinecone search over the embedded KB (PRIMARY, content-based) -- and, when the
     exam is known, scoped to it, so a shared id grounds on the right exam.
  2. exact question-id match over the bundled JSON (offline fallback).
Falls back gracefully when Pinecone is not configured or unreachable, so grading never breaks.
"""
from __future__ import annotations

import json
import os
import re

from .env import HAS_PINECONE
from .kb.exams import EXAMS
from .kb.pinecone import kb_index
from .llm import StepLog, embed
from .models import NotesChunk, Retrieved, SolutionEntry

_GEN = json.load(open(os.path.join(os.path.dirname(__file__), "kb", "generation.json"), encoding="utf-8"))

# Minimum cosine similarity to trust a semantic match -- below this we'd rather ground on
# nothing (and let the Grader escalate) than on the wrong problem.
MIN_SCORE = 0.5
# Notes chunks score lower than curated solutions -- a gentler bar for supporting context.
NOTES_MIN_SCORE = 0.3

_HE_PARTS = {"א": "a", "ב": "b", "ג": "c", "ד": "d", "ה": "e"}


def _norm_id(s: str) -> str:
    """Normalize a question label to a comparable key: "Q3(c)" / "3ג" / "Q3c" -> "3c"."""
    out = (s or "").lower()
    for he, en in _HE_PARTS.items():
        out = out.replace(he, en)
    # ASCII-only, like the TS /[^a-z0-9]/ -- Python's str.isalnum() would keep Hebrew.
    out = re.sub(r"[^a-z0-9]", "", out)  # drop parens, spaces, punctuation, unmapped Hebrew
    return out[1:] if out.startswith("q") else out  # "q3c" and "3c" both -> "3c"


def _looks_like_sin_sqrt_limit(text: str) -> bool:
    """Signature for the Q3c demo limit -- offline safety net when the parsed id is
    unreliable AND Pinecone is unavailable."""
    t = (text or "").lower()
    return ("sin" in t) and ("√" in t or "sqrt" in t) and ("x^9" in t or "x9" in t or "x⁹" in t)


def _find_exact(want: str, exam: str | None) -> Retrieved | None:
    for kb in EXAMS:
        # When the teacher named the exam, only its own questions may ground the grade.
        if exam and kb.exam != exam:
            continue
        for key, entry in kb.questions.items():
            if _norm_id(key) == want or _norm_id(entry.id) == want:
                return Retrieved(entry=entry, exam=kb.exam, course=kb.course)
    return None


def _entry_from_metadata(m: dict) -> Retrieved:
    return Retrieved(
        entry=SolutionEntry(
            id=m.get("entryId", ""), points=int(m.get("points", 0) or 0),
            topic=m.get("topic") or None, problem=m.get("problem", ""),
            official_solution=m.get("official_solution", ""),
            final_answer=m.get("final_answer") or None, notes=m.get("notes") or None,
        ),
        exam=m.get("exam", ""), course=m.get("course", ""),
    )


def _find_semantic(text: str, exam: str | None):
    """Pinecone semantic query over SOLUTION records only. Returns {"found","score"} or None.
    Never raises -- a Pinecone outage must not break grading."""
    try:
        vector = embed(text[:4000])[0]
        # Pin to the current indexing generation (superseded vectors stay physically in the
        # index); scope to the exam when known, since ids/topics repeat across exams.
        flt: dict = {"kind": {"$eq": "solution"}, "gen": {"$eq": _GEN["gen"]}}
        if exam:
            flt["exam"] = {"$eq": exam}
        res = kb_index().query(top_k=1, vector=vector, include_metadata=True, filter=flt)
        matches = res.get("matches") if isinstance(res, dict) else res.matches
        top = matches[0] if matches else None
        score = (top.get("score") if isinstance(top, dict) else getattr(top, "score", 0)) if top else 0
        meta = (top.get("metadata") if isinstance(top, dict) else getattr(top, "metadata", None)) if top else None
        if not meta or (score or 0) < MIN_SCORE:
            return {"found": None, "score": score or 0}
        return {"found": _entry_from_metadata(dict(meta)), "score": score or 0}
    except Exception:
        return None  # treat as "Pinecone unavailable"


def retrieve_notes(question_text: str, log: StepLog, k: int = 3) -> list[NotesChunk]:
    """Top-k course-notes chunks for extra grounding. Independent of solution retrieval;
    returns [] when Pinecone is off/unreachable."""
    if not HAS_PINECONE or not question_text.strip():
        return []
    chunks: list[NotesChunk] = []
    try:
        vector = embed(question_text[:4000])[0]
        res = kb_index().query(top_k=k, vector=vector, include_metadata=True,
                               filter={"kind": {"$eq": "notes"}})
        matches = res.get("matches") if isinstance(res, dict) else res.matches
        for m in matches or []:
            score = m.get("score") if isinstance(m, dict) else getattr(m, "score", 0)
            meta = m.get("metadata") if isinstance(m, dict) else getattr(m, "metadata", None)
            if (score or 0) >= NOTES_MIN_SCORE and meta:
                meta = dict(meta)
                chunks.append(NotesChunk(source=meta.get("source", ""), page=int(meta.get("page", 0) or 0),
                                         text=meta.get("text", ""), score=score or 0))
    except Exception:
        chunks = []
    log.add("Retriever",
            "Retrieve relevant course lecture material (RAG) to give the Grader extra "
            "grounding beyond the official solution. Do not grade.",
            f"Notes query: {question_text[:160]}",
            {"kind": "notes", "hits": [{"page": c.page, "score": c.score} for c in chunks]}, "RAG")
    return chunks


def retrieve(question_id: str, question_text: str, log: StepLog, exam: str | None = None) -> Retrieved | None:
    want = _norm_id(question_id)
    found: Retrieved | None = None
    method = ""
    score = None

    # Primary: semantic vector search, scoped to the exam when known.
    if HAS_PINECONE and question_text.strip():
        sem = _find_semantic(question_text, exam)
        if sem and sem["found"]:
            found, method, score = sem["found"], "semantic", sem["score"]
        elif sem:
            score = sem["score"]  # queried, but below threshold -- record why

    # Fallback (offline / Pinecone miss): exact question-id over the bundled KB.
    if not found:
        found = _find_exact(want, exam)
        if found:
            method = "exact-id"

    # Last resort (offline): the hard-coded signature for the demo limit.
    if not found and _looks_like_sin_sqrt_limit(question_text):
        entry = EXAMS[0].questions.get("Q3c")
        if entry:
            found = Retrieved(entry=entry, exam=EXAMS[0].exam, course=EXAMS[0].course)
            method = "signature"

    if found:
        response = {"matched": found.entry.id, "method": method, "score": score,
                    "exam": found.exam, "course": found.course, "points": found.entry.points,
                    "final_answer": found.entry.final_answer}
    else:
        response = {"matched": None, "method": "searched" if HAS_PINECONE else "no-vector-db",
                    "score": score, "reason": "no knowledge-base entry above the match threshold"}
    log.add("Retriever",
            "Match the exam question to the knowledge base and return the official solution "
            "and point value for grounding the grade. Do not grade.",
            f"Question {question_id}: {(question_text or '')[:200]}", response, "RAG")
    return found
