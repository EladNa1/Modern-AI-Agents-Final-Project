"""Grader module -- scores one question. Port of lib/agent/grader.ts.

Prompt engineering (deck slide 8): PERSONA · CHAIN-OF-THOUGHT · FEW-SHOT · STRUCTURED OUTPUT.
It grades the student's OWN method, grounded strictly in the retrieved official solution,
and awards partial credit -- it does not invent a rubric.

Inspect the prompt (no API call):  python -m checkmate.grader
Grade the built-in sample live:     python -m checkmate.grader --live
"""
from __future__ import annotations

import math
import sys

from .llm import StepLog, chat, extract_json
from .models import Grade, NotesChunk, ParsedFragment, Retrieved, Usage

GRADER_SYSTEM = """PERSONA
You are a senior teaching assistant grading Technion Calculus 1 (Hedva 1) exams. You are rigorous, fair, and consistent — the same mistake always costs the same. You receive ONE question at a time: the student's transcribed work and the official solution.

REASONING (think step by step, internally)
1. Restate the method the STUDENT actually used (grade their approach, not only the official one — alternative valid methods earn full credit).
2. Compare it to the official solution and the correct final answer.
3. Identify exactly where credit is lost (a wrong step, a lost sign, an unjustified claim, a missing hypothesis).
4. Award partial credit proportional to how much correct mathematical work is present. A correct method with one local error keeps most of the credit.

FEW-SHOT (calibration example)
Student: d/dx[x^2 * ln x] = 2x * ln x.
Grading: product rule started correctly but the second term x^2*(1/x)=x is missing. Method sound, one term dropped -> 3 / 5.

GROUNDING
Ground every judgement in the official solution provided. Do NOT invent facts or a different correct answer. If the student's handwriting/transcription is too ambiguous to grade fairly, or no official solution is available, do NOT guess — set "status":"escalate".

OUTPUT
Return ONLY a JSON object, no prose, no code fences:
{"score": <number 0..max>, "max": <max points>, "status": "ok|partial|escalate", "feedback": "<short, specific, student-facing: what was right, where credit was lost>", "justification": "<one line tying the score to the official solution>", "confidence": <0..1>}
Rules: status "ok" only if score == max; "partial" if 0 < score < max; "escalate" if you cannot grade fairly."""


def build_grader_user(q: ParsedFragment, retrieved: Retrieved | None,
                      notes: list[NotesChunk]) -> tuple[str, int]:
    """Assemble the user message (grounding + student work). Returns (message, max_points).
    Pure and side-effect-free, so it can be inspected without any API call."""
    max_points = retrieved.entry.points if retrieved else 0

    if retrieved:
        e = retrieved.entry
        grounding = "\n\n".join(part for part in [
            f"Exam: {retrieved.exam} (course {retrieved.course})",
            f"Question {e.id} — worth {e.points} points.",
            f"Official problem:\n{e.problem}",
            f"Official solution:\n{e.official_solution}",
            f"Correct final answer: {e.final_answer}" if e.final_answer else "",
            f"Grading note: {e.notes}" if e.notes else "",
        ] if part)
    else:
        grounding = "No official solution was retrieved for this question."

    student = "\n".join(part for part in [
        f"Student's transcribed work (Parser confidence {q.confidence:.2f}):",
        q.text,
        f"\nLaTeX:\n{q.latex}" if q.latex else "",
    ] if part)

    # Optional supporting context from the course notes (RAG). Advisory only -- the official
    # solution stays the authority for the correct answer and points.
    notes_block = ""
    if notes:
        joined = "\n\n".join(f"[{n.source} p.{n.page}] {n.text}" for n in notes)
        notes_block = f"\n\n=== COURSE MATERIAL (supporting context, not authoritative) ===\n{joined}"

    user = (
        f"Grade question {q.id}. Maximum score: {max_points} points.\n\n"
        f"=== OFFICIAL SOLUTION (grounding) ===\n{grounding}{notes_block}\n\n"
        f"=== STUDENT WORK ===\n{student}\n\nReturn only the JSON object."
    )
    return user, max_points


def run_grader(q: ParsedFragment, retrieved: Retrieved | None, log: StepLog,
               notes: list[NotesChunk] | None = None) -> Grade:
    notes = notes or []
    user, max_points = build_grader_user(q, retrieved, notes)
    text, usage = chat(GRADER_SYSTEM, user, max_tokens=1200, json_mode=True)
    return _normalize_grade(q.id, max_points, q.confidence, text, usage, log, user)


def _clamp(v, lo: float, hi: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(n):
        return lo
    return max(lo, min(hi, n))


def _normalize_grade(qid: str, max_points: int, parser_confidence: float,
                     text: str, usage: Usage, log: StepLog, user: str) -> Grade:
    raw = extract_json(text)

    # Could not parse a grade, or nothing to ground on -> escalate, never guess.
    if not raw or max_points == 0:
        grade = Grade(
            id=qid, score=0, max=max_points, status="escalate",
            feedback="Could not produce a reliable grade (unparseable model output or missing "
                     "official solution). Sent to a human teacher.",
            justification="Escalated — not graded automatically.", confidence=0,
        )
        log.add("Grader", GRADER_SYSTEM, user, grade.__dict__, "Few-shot", usage)
        return grade

    score = _clamp(raw.get("score"), 0, max_points)
    confidence = _clamp(raw.get("confidence"), 0, 1)

    # Derive a consistent status from score + confidence, overriding a mislabeled one.
    if confidence < 0.4 or parser_confidence < 0.35:
        status = "escalate"
    elif score >= max_points:
        status = "ok"
    elif score > 0:
        status = "partial"
    else:
        status = "partial"
    if str(raw.get("status", "")).lower() == "escalate":
        status = "escalate"

    grade = Grade(
        id=qid, score=score, max=max_points, status=status,
        feedback=(str(raw.get("feedback", "")).strip() or "No feedback provided."),
        justification=str(raw.get("justification", "")).strip(),
        confidence=confidence,
    )
    log.add("Grader", GRADER_SYSTEM, user, grade.__dict__, "Few-shot", usage)
    return grade


if __name__ == "__main__":
    from .models import SolutionEntry

    sample_q = ParsedFragment(id="Q1", text="d/dx[x^2 * ln x] = 2x * ln x", confidence=0.9)
    sample_ret = Retrieved(
        entry=SolutionEntry(
            id="Q1", points=5, problem="Differentiate x^2 ln x.",
            official_solution="Product rule: d/dx[x^2 ln x] = 2x ln x + x^2*(1/x) = 2x ln x + x.",
            final_answer="2x ln x + x",
        ),
        exam="demo", course="104041",
    )
    user, mx = build_grader_user(sample_q, sample_ret, [])

    if "--live" in sys.argv:
        log = StepLog()
        g = run_grader(sample_q, sample_ret, log)
        print(f"score: {g.score}/{g.max}  status: {g.status}  confidence: {g.confidence}")
        print(f"feedback: {g.feedback}")
        print(f"justification: {g.justification}")
        print("tokens:", log.usage)
    else:
        print("=== SYSTEM ===\n" + GRADER_SYSTEM)
        print(f"\n=== USER (max {mx}) ===\n" + user)
        print("\n(dry run — pass --live to actually grade this sample via the LLM)")
