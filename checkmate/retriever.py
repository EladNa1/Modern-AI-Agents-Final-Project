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


def _find_semantic(text: str, exam: str | None, log: StepLog | None = None):
    """Pinecone semantic query over SOLUTION records only. Returns {"found","score"} or None.
    Never raises -- a Pinecone outage must not break grading."""
    try:
        vector = embed(text[:4000], log=log)[0]
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
        vector = embed(question_text[:4000], log=log)[0]
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


_WORD_RE = re.compile(r"[א-ת]{2,}|[a-zA-Z]{3,}|\d+")


def _tokens(text: str) -> set[str]:
    """Comparable token set for content overlap: Hebrew/Latin words + numbers, LaTeX noise
    dropped."""
    t = re.sub(r"\\[a-zA-Z]+|[{}()\[\]$\\]", " ", text or "")
    return set(_WORD_RE.findall(t))


def _find_by_content(question_text: str, exam: str, min_f1: float = 0.5) -> tuple[Retrieved, float] | None:
    """Offline content match, scoped to one exam: F1 overlap between the fragment's tokens
    and each KB question's PRINTED problem text; best above a floor wins. Deterministic and
    free -- covers the Pinecone-absent case where the parser's label ('Q1' on the MC page,
    bare '1' on the T/F page, 'b') maps to nothing -- or to the WRONG entry -- by exact id.
    F1 (not raw containment) so a short generic problem can't outscore the actual one; the
    0.5 floor keeps handwriting-only continuation fragments (no printed header) unmatched
    rather than silently mis-grouped."""
    frag = _tokens(question_text)
    if len(frag) < 4:
        return None
    best: tuple[Retrieved, float] | None = None
    for kb in EXAMS:
        if kb.exam != exam:
            continue
        for entry in kb.questions.values():
            prob = _tokens(entry.problem)
            if len(prob) < 4:
                continue
            inter = len(frag & prob)
            f1 = 2 * inter / (len(frag) + len(prob))
            if f1 >= min_f1 and (best is None or f1 > best[1]):
                best = (Retrieved(entry=entry, exam=kb.exam, course=kb.course), f1)
    return best


def retrieve(question_id: str, question_text: str, log: StepLog, exam: str | None = None) -> Retrieved | None:
    want = _norm_id(question_id)
    found: Retrieved | None = None
    method = ""
    score = None

    # Canonical T/F- and MC-numbered ids come from the deterministic page split (the printed
    # item numbering), not from the parser's guess -- for these, the exact-id match IS the
    # ground truth and outranks semantic search (whose near-identical short statements
    # otherwise cross-match siblings: one statement absorbs another's fragment).
    if re.fullmatch(r"(tf|mc)\d+", want):
        found = _find_exact(want, exam)
        if found:
            method = "exact-id"

    # Primary: semantic vector search, scoped to the exam when known. Content is used to
    # match (not the parser's question-number label, which is unreliable -- it may read a
    # "שאלה 2" part as Q1); scoping to the exam already prevents drift onto another exam's
    # look-alike question, and the exact-id match below is only a fallback.
    if not found and HAS_PINECONE and question_text.strip():
        sem = _find_semantic(question_text, exam, log)
        if sem and sem["found"]:
            found, method, score = sem["found"], "semantic", sem["score"]
        elif sem:
            score = sem["score"]  # queried, but below threshold -- record why

    # Scoped offline content match: the booklet prints the question text above the student's
    # work, so overlap with the KB problem text identifies the question even when the parser's
    # label lies ('Q3' on the MC page must map to MC-3, not the open Q3). A strong content hit
    # therefore OUTRANKS the exact-id fallback; exact-id still covers handwriting-only
    # fragments whose label is right but which restate no printed text.
    if not found and exam:
        hit = _find_by_content(question_text, exam)
        if hit:
            found, score = hit[0], round(hit[1], 3)
            method = "content-overlap"

    # Fallback (offline / Pinecone miss): exact question-id over the bundled KB.
    if not found:
        found = _find_exact(want, exam)
        if found:
            method = "exact-id"

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
