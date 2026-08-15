"""Router scope guard -- decides whether a request is a Calculus-1 grading task at all,
BEFORE any model call. Off-topic requests get a polite refusal with zero LLM tokens spent
(the whole point: don't burn budget interpreting a sourdough question as an exam).

Two tiers, because "mentions math" is NOT "asks for grading":
  - GRADING intent (grade/check/score/exam/booklet...) -> proceed to the agent.
  - math content WITHOUT grading intent ("solve this limit for me") -> refuse: CheckMate
    grades exams, it does not solve or tutor.
  - neither -> refuse as fully out-of-domain.

The decision is logged as a Router step so the refusal shows up in the steps[] trace as an
explicit autonomous decision, consistent with the architecture diagram.
"""
from __future__ import annotations

import re

# Grading intent: verbs and exam artifacts (English + Hebrew). Any of these means the user
# is asking for grading work, so the request proceeds into the agent.
_GRADING_RE = re.compile(
    r"grade|grading|score|scoring|mark(?:ing)?|check|feedback|partial credit|"
    r"exam|test|quiz|booklet|scan|moed|final|midterm|sample|"
    r"104041|104042|104195|"
    r"בדיקה|בדוק|בדקי|תבדוק|לבדוק|ציון|ציונים|נקודות|ניקוד|משוב|"
    r"מבחן|בוחן|מועד|טופס|מחברת|סריקה|סרוק",
    re.IGNORECASE)

# Course-domain content words alone (no grading intent) signal a solve/tutor/explain
# request -- polite refusal with a scope explanation, not a grading attempt.
_MATH_RE = re.compile(
    r"calculus|hedva|limit|derivative|integral|theorem|proof|converge|series|"
    r"חדו\"?א|חשבון (?:אינפי|דיפרנציאלי)|אינפי|גבול|נגזרת|אינטגרל|משפט|הוכחה|טור|התכנס",
    re.IGNORECASE)


def classify(prompt: str) -> str:
    """'grading' | 'math_no_grading' | 'offtopic'."""
    p = prompt or ""
    if _GRADING_RE.search(p):
        return "grading"
    if _MATH_RE.search(p):
        return "math_no_grading"
    return "offtopic"


def in_scope(prompt: str) -> bool:
    return classify(prompt) == "grading"


_REFUSALS = {
    "offtopic": (
        "I can't help with that — CheckMate only grades scanned Technion Calculus 1 (Hedva 1) "
        "exams.\n\n"
        "What I CAN do: read a scanned exam booklet (PDF or images), grade every question on "
        "the student's own method with partial credit and written feedback, ground each grade "
        "in the official solution, and escalate anything ambiguous to a human teacher.\n\n"
        "To use me: upload an exam scan (or POST it to /api/execute as multipart form-data "
        "under `file`), or name a bundled sample booklet in your prompt. Your request didn't "
        "look like a Calculus 1 grading task, so no grading model was invoked and no tokens "
        "were spent.",
        "The request does not mention grading, an exam, or Calculus 1 material."),
    "math_no_grading": (
        "I can't help with that — CheckMate GRADES Calculus 1 exams; it does not solve "
        "exercises, tutor, or explain course material.\n\n"
        "If you want a student's written work assessed: upload the scanned exam (multipart "
        "`file` on /api/execute) or name a bundled sample booklet, and I'll grade it against "
        "the official solutions with partial credit and feedback.\n\n"
        "Your request asked for math help rather than grading, so no grading model was "
        "invoked and no tokens were spent.",
        "Math content without grading intent — a solve/tutor request, which is out of scope."),
}


def refusal_payload(prompt: str, kind: str | None = None) -> dict:
    """Spec-shaped /api/execute response for an out-of-scope request."""
    kind = kind or classify(prompt)
    text, reason = _REFUSALS.get(kind, _REFUSALS["offtopic"])
    step = {
        "module": "Router",
        "pattern": "Scope guard",
        "prompt": {
            "System_prompt": (
                "Decide whether the incoming request is a Calculus 1 exam-grading task. "
                "If it is not (off-domain, or a solve/tutor request), refuse politely and "
                "stop -- do not invoke the Parser, Retriever, Grader, or Reflector, and do "
                "not spend model tokens on an out-of-domain request."),
            "User_prompt": (prompt or "")[:400],
        },
        "response": {
            "in_scope": False,
            "decision": "REFUSE",
            "reason": reason,
            "llm_calls": 0,
            "tokens_spent": 0,
        },
    }
    return {
        "status": "ok",
        "error": None,
        "response": text,
        "steps": [step],
        "meta": {"mode": "refused", "source": None, "total": 0, "max": 0, "questions": []},
    }
