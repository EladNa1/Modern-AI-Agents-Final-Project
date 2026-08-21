"""Reflector module -- the self-critique stage that makes CheckMate a Reflection Agent.
It critiques a proposed grade against the retrieved official solution and chooses ONE
action: APPROVE · REVISE (corrected grade) · ESCALATE. Port of lib/agent/reflector.ts.
"""
from __future__ import annotations

from .config import CONFIG
from .env import LLMOD_GRADER_MODEL
from .llm import StepLog, chat, extract_json
from .models import Grade, ParsedFragment, Reflection, Retrieved, Usage, canonical_qid

REFLECTOR_SYSTEM = """You are the Reflector module of CheckMate, grading Technion Calculus 1 exams. You receive a PROPOSED grade for one question, the student's transcribed work, and the official solution. Critique the proposed grade against that evidence — do not re-grade from scratch, judge whether the grade is fair and grounded.

Check:
- Is the score justified by the official solution and consistent partial-credit reasoning?
- Is credit for the student's OWN valid method preserved (alternative correct methods deserve full credit)?
- Is the student's work too ambiguous/illegible to grade fairly?

Choose EXACTLY ONE action:
- APPROVE — the grade is fair and grounded; keep it.
- REVISE — the grade is wrong or unfair; give a corrected score and feedback.
- ESCALATE — the work cannot be graded fairly (ambiguous handwriting or genuine uncertainty); send to a human teacher. Never guess.

Output ONLY a JSON object, no prose, no code fences:
{"action":"APPROVE|REVISE|ESCALATE","score":<number 0..max, or null>,"feedback":"<corrected student-facing feedback if REVISE, else empty>","note":"<one line: why>","confidence":<0..1>}
Write "feedback" and "note" in ENGLISH, always (student's Hebrew may be quoted verbatim)."""


def run_reflector(q: ParsedFragment, grade: Grade, retrieved: Retrieved | None, log: StepLog) -> Reflection:
    if retrieved:
        e = retrieved.entry
        note_line = f"Grading note: {e.notes}" if e.notes else ""
        grounding = (f"Question {e.id} — worth {e.points} points.\n\n"
                     f"Official solution:\n{e.official_solution}\n\n"
                     f"Correct final answer: {e.final_answer or 'n/a'}\n{note_line}")
    else:
        grounding = "No official solution was retrieved."

    latex_line = f"\nLaTeX:\n{q.latex}" if q.latex else ""
    user = (
        f"Question {canonical_qid(q, retrieved)} (max {grade.max} points).\n\n"
        f"=== PROPOSED GRADE ===\n"
        f"score: {grade.score}/{grade.max} (status {grade.status}, confidence {grade.confidence})\n"
        f"feedback: {grade.feedback}\n"
        f"justification: {grade.justification}\n\n"
        f"=== OFFICIAL SOLUTION (evidence) ===\n{grounding}\n\n"
        f"=== STUDENT WORK (Parser confidence {q.confidence:.2f}) ===\n{q.text}{latex_line}\n\n"
        f"Critique the proposed grade and return only the JSON object."
    )

    text, usage = chat(REFLECTOR_SYSTEM, user, max_tokens=CONFIG.reflector_max_tokens,
                       json_mode=True, model=LLMOD_GRADER_MODEL)
    return _normalize_reflection(text, grade.max, usage, log, user)


def _normalize_reflection(text: str, max_points: int, usage: Usage, log: StepLog, user: str) -> Reflection:
    raw = extract_json(text)
    # Fail CLOSED: an unparseable critique (or an action outside the contract) must not
    # silently approve the grade it was supposed to check -- hand it to a human instead.
    if not isinstance(raw, dict) or str(raw.get("action", "")).upper() not in (
            "APPROVE", "REVISE", "ESCALATE"):
        broken = Reflection(action="ESCALATE", score=None, feedback="",
                            note="Reflector output was malformed — failing closed to human review.",
                            confidence=0.0)
        log.add("Reflector", REFLECTOR_SYSTEM, user,
                {"action": broken.action, "note": broken.note,
                 "unparseable_output": (text or "")[:400]},
                "Reflection", usage)
        return broken
    action = str(raw.get("action")).upper()

    raw_score = raw.get("score")
    score_num = max(0.0, min(max_points, raw_score)) if isinstance(raw_score, (int, float)) else None

    conf = raw.get("confidence")
    reflection = Reflection(
        action=action,
        score=score_num if action == "REVISE" else None,
        feedback=str(raw.get("feedback", "")).strip(),
        note=str(raw.get("note", "")).strip() or "(no note)",
        confidence=max(0.0, min(1.0, conf)) if isinstance(conf, (int, float)) else 0.5,
    )

    log.add("Reflector", REFLECTOR_SYSTEM, user,
            {"action": reflection.action, "score": reflection.score,
             "feedback": reflection.feedback,  # the revised feedback IS the result of a REVISE
             "note": reflection.note, "confidence": reflection.confidence},
            "Reflection", usage)
    return reflection
