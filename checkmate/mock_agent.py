"""CheckMate -- mock agent. Produces a correctly-shaped /api/execute payload with a
realistic steps[] trace when no LLM key / no image is available. Port of lib/mockAgent.ts.
"""
from __future__ import annotations

import re
from dataclasses import asdict

from .models import QuestionResult


def _clip(s: str, n: int = 600) -> str:
    return s[:n] + " …[truncated]" if len(s) > n else s


# A fixed sample exam graded question-by-question. Stands in for the real pipeline.
SAMPLE_QUESTIONS: list[QuestionResult] = [
    QuestionResult("Q1", "Q1 · Derivative — product rule", 3, 5, "partial", "3/5",
        "Product rule applied to the first term correctly. Missing the second term "
        "x²·(1/x)=x. Method sound, one term omitted → partial credit. Expected 2x·ln x + x."),
    QuestionResult("Q2", "Q2 · Limit — L'Hôpital", 5, 5, "ok", "✓",
        "Indeterminate form 0/0 identified, L'Hôpital applied once, limit = 1. Matches the "
        "rubric solution. Full credit."),
    QuestionResult("Q3", "Q3 · Lagrange MVT — proof", 10, 12, "partial", "10/12",
        "Auxiliary function g(x) constructed correctly and Rolle's theorem invoked. Endpoints "
        "g(a)=g(b)=0 shown. Gap: continuity/differentiability of g not stated before applying "
        "Rolle. −2."),
    QuestionResult("Q4", "Q4 · Integral — substitution", 7, 7, "ok", "✓",
        "u-substitution u=x²+1 chosen, bounds transformed, antiderivative correct against the "
        "rubric solution. Full credit."),
    QuestionResult("Q5", "Q5 · Taylor series — remainder", 4, 5, "escalate", "?",
        "Handwriting on the remainder bound is ambiguous (two overwritten symbols). Parser "
        "confidence low; escalated to the teacher rather than guessed."),
]


def _step(module: str, pattern: str, system: str, user: str, response) -> dict:
    return {"module": module, "pattern": pattern,
            "prompt": {"System_prompt": system, "User_prompt": user}, "response": response}


def _build_steps(source: str, qs: list[QuestionResult], instructions: str) -> list[dict]:
    steps: list[dict] = []
    instr_line = f"\nUser instructions: {_clip(instructions, 200)}" if instructions else ""

    steps.append(_step(
        "Parser", "Vision OCR",
        "Read all pages of the scanned Calculus 1 exam. Return each question's handwritten "
        "work as text/LaTeX, with a confidence score per question. Do not grade.",
        f"[exam file: {_clip(source, 120)} — pages 1–4]{instr_line}",
        {"questions": [{"id": q.id, "confidence": 0.58 if q.status == "escalate" else 0.94} for q in qs]},
    ))

    for q in qs:
        steps.append(_step("Retriever", "RAG",
            "Match this question to the knowledge base: pull the official solution, rubric, "
            "and graded examples.", f"{q.id}: {q.title}",
            {"matched": f"rubric/{q.id}", "graded_examples": 2, "max_points": q.max}))
        steps.append(_step("Grader", "Few-shot",
            "Grade the student's OWN method against the retrieved rubric and graded examples. "
            "Award partial credit. Return score, max, and written feedback.",
            f"Grade {q.id} from OCR text + retrieved rubric.",
            {"score": q.score, "max": q.max, "feedback": _clip(q.feedback, 300)}))
        if q.status in ("partial", "escalate"):
            action = "ESCALATE" if q.status == "escalate" else "APPROVE"
            steps.append(_step("Reflector", "Reflection",
                "The grade looks shaky (borderline partial credit, or low OCR confidence, or "
                "score/feedback disagree). Critique it and choose exactly ONE action: "
                "APPROVE · REVISE(≤1) · RETRY_OCR(≤1) · ESCALATE.",
                f"Review {q.id}: {q.score}/{q.max}, OCR confidence {0.58 if q.status == 'escalate' else 0.94}.",
                {"action": action,
                 "note": "OCR confidence 0.58 on the remainder bound — send to teacher."
                         if action == "ESCALATE" else "Partial credit is justified by the rubric; accept."}))
    return steps


_GENERAL_RE = re.compile(
    r"general|overall|holistic|no scor|no grad|without grad|do ?n['’]?t grade|feedback only|"
    r"כללי|חוו|בלי ציון|בלי לצ|ללא ציון|בלי להתמ")


def _is_general(lc: str) -> bool:
    return bool(_GENERAL_RE.search(lc))


def _requested_questions(instr: str) -> list[str]:
    ids: list[str] = []
    for m in re.finditer(r"(?:q|question|שאל\w*)\s*\.?\s*(\d+)", instr, re.IGNORECASE):
        qid = "Q" + m.group(1)
        if qid not in ids:
            ids.append(qid)
    return ids


def run_mock_agent(source: str, instructions: str = "") -> dict:
    if not source or not source.strip():
        return {"status": "error",
                "error": "No exam provided. Upload a scanned exam (PDF, Word, or image).",
                "response": None, "steps": []}

    instr = (instructions or "").strip()
    lc = instr.lower()

    # ---- General-feedback mode: holistic opinion, no per-question scoring. ----
    if instr and _is_general(lc):
        steps = [
            _step("Parser", "Vision OCR",
                "Read all pages of the scanned Calculus 1 exam and return the student's work "
                "as text/LaTeX. Do not grade.",
                f"[exam file: {_clip(source, 120)}]\nUser instructions: {_clip(instr, 200)}",
                {"questions_read": len(SAMPLE_QUESTIONS)}),
            _step("Grader", "Few-shot",
                "The user asked for a GENERAL opinion of the student's exam — no per-question "
                "scores. Write a short holistic assessment: overall command of the material, "
                "strengths, recurring weaknesses, and one suggestion. Do not output numeric grades.",
                f"Give a general assessment of the exam.\nUser instructions: {_clip(instr, 200)}",
                {"assessment": "Solid grasp of core techniques (product rule, L'Hôpital, "
                 "substitution). Main weakness: stating theorem hypotheses before applying them, "
                 "and completing multi-term derivatives. One answer's handwriting is unclear."}),
        ]
        return {
            "status": "ok", "error": None,
            "response": (
                "General assessment (no scoring, as requested):\n\n"
                "The student shows a solid command of the core Calculus 1 techniques — the "
                "product rule, L'Hôpital's rule, and u-substitution are applied correctly. "
                "The recurring weakness is rigor around theorem hypotheses (e.g. stating "
                "continuity/differentiability before invoking Rolle's/Lagrange's theorem) and "
                "occasionally dropping a term in multi-term derivatives. One answer's handwriting "
                "is ambiguous and would benefit from a closer read. Overall: a capable student "
                "who would gain the most from tightening proof rigor.\n\n"
                "Note: demo build — grading is simulated."),
            "steps": steps,
            "meta": {"total": 0, "max": 0, "questions": [], "source": source,
                     "mode": "general", "instructions": instr},
        }

    # ---- Full or subset grading. ----
    wanted = _requested_questions(instr)
    subset = len(wanted) > 0
    qs = [q for q in SAMPLE_QUESTIONS if q.id in wanted] if subset else list(SAMPLE_QUESTIONS)

    if subset and not qs:
        have = ", ".join(q.id for q in SAMPLE_QUESTIONS)
        return {"status": "error", "error": f"Requested {', '.join(wanted)}, but the exam has {have}.",
                "response": None, "steps": []}

    steps = _build_steps(source, qs, instr)
    total = sum(q.score for q in qs)
    max_pts = sum(q.max for q in qs)
    escalated = [q for q in qs if q.status == "escalate"]

    header = (f"CheckMate graded {', '.join(q.id for q in qs)}: {total} / {max_pts}."
              if subset else f"CheckMate graded the exam: {total} / {max_pts}.")
    lines = [
        header, "",
        *[f"{q.id} — {q.score}/{q.max}: {q.feedback}" for q in qs], "",
        (f"⚠ {len(escalated)} question(s) escalated to the teacher: "
         f"{', '.join(q.id for q in escalated)}.") if escalated else "No questions required human review.",
        "",
        "Note: demo build — grading is simulated. The real pipeline runs OCR, Pinecone RAG, "
        "and gpt-5.4-mini grading, and returns the annotated exam.",
    ]

    return {
        "status": "ok", "error": None, "response": "\n".join(lines), "steps": steps,
        "meta": {"total": total, "max": max_pts, "questions": [asdict(q) for q in qs],
                 "source": source, "mode": "subset" if subset else "full", "instructions": instr},
    }
