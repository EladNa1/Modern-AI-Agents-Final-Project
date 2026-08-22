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

# Exam ARTIFACTS (nouns, word-bounded in English): a prompt naming one of these is about
# an exam, so grading intent is established on its own.
_EXAM_NOUN_RE = re.compile(
    r"\b(exam|quiz|booklet|scan|moed|final|midterm|sample)s?\b|"
    # bare "test" is too generic ("driving test", "test my patience") -- require course or
    # grading context around it
    r"\b(?:calculus|math|scanned|graded|this)\s+test\b|\btest\s+booklet\b|"
    r"104041|104042|104195|"
    r"מבחן|בוחן|מועד|טופס|מחברת|סריקה",
    re.IGNORECASE)

# Grading VERBS (word-bounded in English). A bare verb is NOT enough -- "please CHECK why
# my car makes a grinding noise" is not a grading request. A verb counts only alongside
# course-domain content (see classify below).
_GRADING_VERB_RE = re.compile(
    r"\b(grade|grading|regrade|score|scoring|mark|marking|check|assess|evaluate|review)\b|"
    r"בדיקה|בדוק|בדקי|תבדוק|לבדוק|ציון|ציונים|נקודות|ניקוד|משוב|סרוק",
    re.IGNORECASE)

# Course-domain content words. Alone (no grading intent) they signal a solve/tutor/explain
# request -- polite refusal with a scope explanation, not a grading attempt.
_MATH_RE = re.compile(
    r"calculus|hedva|limit|derivative|integral|theorem|proof|converge|series|"
    r"חדו\"?א|חשבון (?:אינפי|דיפרנציאלי)|אינפי|גבול|נגזרת|אינטגרל|משפט|הוכחה|טור|התכנס",
    re.IGNORECASE)

# Tutoring intent beats everything except an actual exam artifact: "teach me", "explain",
# "step by step" describe a lesson, not a grading job -- even when grading verbs appear.
_TUTOR_RE = re.compile(
    r"\b(teach|explain|tutor|walk me through|show me how|step[- ]by[- ]step|how (?:do|to))\b|"
    # imperative solve/answer requests ("Solve this exam question for me") are lesson
    # requests too -- an object determiner keeps this from firing on descriptive uses
    r"\b(?:solve|answer|compute|calculate)\s+(?:this|that|the|my|these|a|an)\b|"
    r"תלמד|למד אותי|תסביר|הסבר|צעד אחר צעד|פתור|תפתור|פתרי|לפתור",
    re.IGNORECASE)

# Explicit negation of grading ("do not grade", "without grading", "instead of grading") --
# the user is telling us the one thing we do is NOT wanted. "instead of" is its own idiom:
# it negates by SUBSTITUTION rather than by a negative particle ("tutor me ... instead of
# grading an exam"), so it needs its own alternation -- without it the bare noun "exam" won
# and the request was routed as a grading job.
_NO_GRADING_RE = re.compile(
    r"\b(?:do\s*n[o']t|don't|without|no)\s+(?:grade|grading|scoring|marking)\b|"
    r"\binstead\s+of\s+(?:grade|grading|scoring|marking|checking)\b|"
    r"\brather\s+than\s+(?:grade|grading|scoring|marking|checking)\b|"
    r"במקום (?:לבדוק|בדיקה|לתת ציון)|"
    r"בלי (?:לבדוק|ציון|בדיקה)|לא לבדוק|אל תבדוק|ללא ציון",
    re.IGNORECASE)

# The negated clause INCLUDING its object ("do not grade an exam") -- stripped before the
# exam-noun test, so the noun inside the negation cannot establish grading scope on its own.
_NO_GRADING_STRIP_RE = re.compile(
    r"\b(?:do\s*n[o']t|don't|without|no)\s+(?:grade|grading|score|scoring|mark|marking)\s*"
    r"(?:an?y?\s+|the\s+|my\s+|this\s+)?(?:exam|test|quiz|booklet|scan|anything)?s?\b|"
    r"\b(?:instead\s+of|rather\s+than)\s+(?:grade|grading|score|scoring|mark|marking|checking)\s*"
    r"(?:an?y?\s+|the\s+|my\s+|this\s+)?(?:exam|test|quiz|booklet|scan|anything)?s?\b|"
    r"במקום\s*(?:לבדוק|בדיקת|בדיקה|לתת ציון)\s*(?:את\s+)?(?:ה?מבחן|ה?בוחן|ה?מחברת)?|"
    r"(?:בלי|לא|אל|ללא)\s*(?:לבדוק|תבדוק|בדיקת|בדיקה|ציון)\s*(?:את\s+)?"
    r"(?:ה?מבחן|ה?בוחן|ה?מחברת|כלום|שום דבר)?",
    re.IGNORECASE)


def classify(prompt: str) -> str:
    """'grading' | 'math_no_grading' | 'offtopic'.

    grading  = an exam artifact is named, OR a grading verb appears together with
               course-domain content ("check my solution to this integral").
    math     = course-domain content without grading intent (solve/tutor request).
    offtopic = everything else -- including grading verbs about non-exam things
               ("check why my car is making a noise")."""
    p = prompt or ""
    has_math = bool(_MATH_RE.search(p))
    negated = bool(_NO_GRADING_RE.search(p))
    # A tutoring frame PLUS an explicit "do not grade" is unambiguously a lesson request --
    # refuse even if an exam is mentioned ("Teach me step by step... Do not grade an exam").
    if negated and _TUTOR_RE.search(p):
        return "math_no_grading" if has_math else "offtopic"
    # Tutor/solve intent outranks a BARE exam noun: "Teach me how to solve this Calculus
    # exam question" is a lesson request that happens to mention an exam. An explicit
    # grading VERB restores scope -- "walk me through grading sample booklet 1" and
    # "how do you grade booklet 1" are still grading jobs.
    if _TUTOR_RE.search(p) and not _GRADING_VERB_RE.search(p):
        return "math_no_grading" if (has_math or _EXAM_NOUN_RE.search(p)) else "offtopic"
    # An exam artifact makes it our domain regardless of phrasing -- "general feedback on
    # my exam, no grading" is the legitimate feedback-only mode, not a refusal case. But the
    # negated clause's own object must not establish scope, so it is stripped first.
    p_active = _NO_GRADING_STRIP_RE.sub(" ", p) if negated else p
    if _EXAM_NOUN_RE.search(p_active):
        return "grading"
    # Without an exam artifact, explicit "do not grade" or a tutoring frame means a lesson
    # is being requested -- the one thing we do is not wanted.
    if _NO_GRADING_RE.search(p) or _TUTOR_RE.search(p):
        return "math_no_grading" if has_math else "offtopic"
    if _GRADING_VERB_RE.search(p) and has_math:
        return "grading"
    if has_math:
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

# Same refusals in Hebrew, served when the request itself is written in Hebrew (still zero
# model tokens -- language detection is one regex over the prompt's characters).
_REFUSALS_HE = {
    "offtopic": (
        "אני לא יכול לעזור בזה — CheckMate בודק אך ורק מחברות בחינה סרוקות של חדו״א 1 "
        "(104041) בטכניון.\n\n"
        "מה שאני כן יודע לעשות: לקרוא מחברת בחינה סרוקה (PDF או תמונות), לתת ציון לכל שאלה "
        "לפי דרך הפתרון של הסטודנט עם ניקוד חלקי ומשוב כתוב, לעגן כל ציון בפתרון הרשמי, "
        "ולהעביר כל מקרה לא ברור למרצה.\n\n"
        "כדי להשתמש בי: העלו סריקת בחינה (או POST ל-/api/execute כ-multipart תחת `file`), "
        "או ציינו בשם אחת מהמחברות המובנות. הבקשה לא נראתה כמשימת בדיקה של חדו״א 1, ולכן "
        "לא הופעל מודל בדיקה ולא נצרכו טוקנים.",
        "The request does not mention grading, an exam, or Calculus 1 material."),
    "math_no_grading": (
        "אני לא יכול לעזור בזה — CheckMate בודק מבחני חדו״א 1; הוא לא פותר תרגילים, לא "
        "מלמד ולא מסביר חומר.\n\n"
        "אם תרצו שעבודה כתובה של סטודנט תיבדק: העלו את הסריקה (multipart `file` על "
        "/api/execute) או ציינו מחברת מובנית בשם, ואבדוק אותה מול הפתרונות הרשמיים עם ניקוד "
        "חלקי ומשוב.\n\n"
        "הבקשה ביקשה עזרה במתמטיקה ולא בדיקה, ולכן לא הופעל מודל ולא נצרכו טוקנים.",
        "Math content without grading intent — a solve/tutor request, which is out of scope."),
}

_HEBREW_RE = re.compile(r"[֐-׿]")


def refusal_payload(prompt: str, kind: str | None = None) -> dict:
    """Spec-shaped /api/execute response for an out-of-scope request. The refusal text
    follows the request's language (Hebrew in -> Hebrew out); the logged reason stays
    English for trace consistency."""
    kind = kind or classify(prompt)
    table = _REFUSALS_HE if _HEBREW_RE.search(prompt or "") else _REFUSALS
    text, reason = table.get(kind, table["offtopic"])
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
