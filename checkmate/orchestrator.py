"""Orchestrator -- runs the CheckMate Reflection Agent end to end and assembles the
brief-shaped result. Port of lib/agent/orchestrator.ts.

Pipeline: Parser (once) -> per question: Retriever -> Grader -> Reflector (revise <= N) ->
approve|escalate. Falls back to the mock when there is no API key or no image to read.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict

from .env import HAS_LLM
from .grader import run_grader
from .llm import StepLog
from .mock_agent import run_mock_agent
from .models import Grade, ImageInput, ParsedFragment, QuestionResult, Retrieved
from .parser import run_parser
from .reflector import run_reflector
from .retriever import retrieve, retrieve_notes

MAX_REVISE_PASSES = 2
UNMATCHED = "Unmatched work"  # bucket for transcribed work that matches no KB question


def run_agent(images: list[ImageInput], instructions: str = "", source_label: str = "",
              exam: str | None = None) -> dict:
    # Fallback: no key configured or nothing to look at -> deterministic mock.
    if not HAS_LLM or not images:
        return run_mock_agent(source_label, instructions or "")

    instructions = (instructions or "").strip()
    log = StepLog()

    try:
        parsed = run_parser(images, log, instructions)
        if not parsed.fragments:
            return {"status": "error",
                    "error": "The Parser could not read any question from the scan. Try a clearer image.",
                    "response": None, "steps": _steps(log)}

        # Group the transcribed fragments by the exam question they actually answer -- the
        # Retriever matches each fragment on CONTENT, and that match defines a question here.
        groups = _group_by_retrieved_question(parsed.fragments, log, exam)

        results: list[QuestionResult] = []
        for group in groups:
            merged = _merge_fragments(group["fragments"])
            q = merged if group["retrieved"] else ParsedFragment(
                id=UNMATCHED, text=merged.text, latex=merged.latex,
                confidence=merged.confidence, page=merged.page)
            retrieved = group["retrieved"]
            query = f"{q.text} {q.latex or ''}"
            notes = retrieve_notes(query, log)
            grade = run_grader(q, retrieved, log, notes)

            # Reflection loop: critique, revise up to N passes, then approve or escalate.
            for _ in range(MAX_REVISE_PASSES):
                refl = run_reflector(q, grade, retrieved, log)
                if refl.action == "APPROVE":
                    break
                if refl.action == "ESCALATE":
                    grade = Grade(**{**grade.__dict__, "status": "escalate"})
                    break
                grade = _apply_revision(grade, refl.score, refl.feedback)  # REVISE

            results.append(_to_question_result(q.id, grade, retrieved))

        return _assemble(results, source_label, instructions, log)
    except Exception as err:
        # Any gateway/parse failure -> a valid error payload (never raise to the route).
        return {"status": "error", "error": "The agent failed while grading: " + str(err),
                "response": None, "steps": _steps(log)}


def _group_by_retrieved_question(fragments: list[ParsedFragment], log: StepLog, exam: str | None) -> list[dict]:
    """Retrieve once per fragment and bucket fragments by the question they matched.
    Fragments that match nothing go into ONE bucket -- reported and escalated, not attached
    to a real question."""
    matches = [(f, retrieve(f.id, f"{f.text} {f.latex or ''}", log, exam)) for f in fragments]

    # Cross-exam consistency: one uploaded booklet is one exam. When the run was NOT scoped
    # (Auto) and the fragments mostly matched a single exam, pin the outliers to that majority
    # exam and re-retrieve them there. A stray match to a look-alike question in another exam
    # is worse than dropping the fragment to "unmatched", so keep whatever the scoped retry
    # returns (possibly nothing) rather than the cross-exam guess.
    if not exam:
        matched_exams = [r.exam for _, r in matches if r]
        if matched_exams:
            majority = Counter(matched_exams).most_common(1)[0][0]
            matches = [
                (f, retrieve(f.id, f"{f.text} {f.latex or ''}", log, majority)
                    if (r and r.exam != majority) else r)
                for f, r in matches
            ]

    groups: dict[str, dict] = {}
    for f, retrieved in matches:
        key = retrieved.entry.id if retrieved else UNMATCHED
        if key in groups:
            groups[key]["fragments"].append(f)
        else:
            groups[key] = {"key": key, "retrieved": retrieved, "fragments": [f]}
    return list(groups.values())


def _merge_fragments(fragments: list[ParsedFragment]) -> ParsedFragment:
    """Stitch every page of one answer back into a single body of work to grade."""
    ordered = sorted(fragments, key=lambda f: f.page)
    first = ordered[0]
    if len(ordered) == 1:
        return first
    return ParsedFragment(
        id=first.id,
        text="\n".join(f.text for f in ordered if f.text),
        latex=("\n".join(f.latex for f in ordered if f.latex) or None),
        confidence=min(f.confidence for f in ordered),
        page=first.page,
    )


def _apply_revision(grade: Grade, new_score: float | None, new_feedback: str) -> Grade:
    score = max(0.0, min(grade.max, new_score)) if new_score is not None else grade.score
    status = "ok" if score >= grade.max else "partial"
    return Grade(**{**grade.__dict__, "score": score, "status": status,
                    "feedback": new_feedback.strip() or grade.feedback})


def _to_question_result(qid: str, grade: Grade, retrieved: Retrieved | None) -> QuestionResult:
    # Prefer the KB's canonical id -- the Parser's guess is unreliable when the scan shows
    # only one part (e.g. just "(ג)").
    display_id = retrieved.entry.id if retrieved else qid
    topic = retrieved.entry.topic if retrieved else None
    title = f"{display_id} · {topic}" if topic else display_id
    mark = "✓" if grade.status == "ok" else ("?" if grade.status == "escalate" else f"{grade.score}/{grade.max}")
    return QuestionResult(id=display_id, title=title, score=grade.score, max=grade.max,
                          status=grade.status, mark=mark, feedback=grade.feedback)


def _assemble(questions: list[QuestionResult], source: str, instructions: str, log: StepLog) -> dict:
    total = sum(q.score for q in questions)
    max_pts = sum(q.max for q in questions)
    escalated = [q for q in questions if q.status == "escalate"]

    lines = [
        f"CheckMate graded {', '.join(q.id for q in questions)}: {total} / {max_pts}.", "",
        *[f"{q.id} — {q.score}/{q.max}: {q.feedback}" for q in questions], "",
        (f"⚠ {len(escalated)} question(s) escalated to the teacher: "
         f"{', '.join(q.id for q in escalated)}.") if escalated else "No questions required human review.",
    ]

    return {
        "status": "ok", "error": None, "response": "\n".join(lines), "steps": _steps(log),
        "meta": {"total": total, "max": max_pts, "questions": [asdict(q) for q in questions],
                 "source": source, "mode": "full", "instructions": instructions},
    }


def _steps(log: StepLog) -> list[dict]:
    return [asdict(s) for s in log.steps]
