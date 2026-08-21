"""Shared data types for the pipeline.

The TS version spreads these across parser.ts / retriever.ts / grader.ts; collecting them
in one module avoids circular imports between the Python pipeline stages. `Usage` stays a
plain dict {"prompt","completion","total"} so the StepLog can accumulate it cheaply.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Usage = dict  # {"prompt": int, "completion": int, "total": int}


@dataclass
class ImageInput:
    data_url: str
    detail: str = "high"  # "low" | "high" | "auto"


@dataclass
class ParsedFragment:
    """One block of student work found on one page. `id` is only the label as read from the
    handwriting -- a hint, not an identity; the Retriever decides the real question."""
    id: str
    text: str
    confidence: float
    latex: str | None = None
    page: int = 1
    bbox: list | None = None  # optional normalized [x0,y0,x1,y1] of this work, for zoom Pass 2


@dataclass
class SolutionEntry:
    id: str
    points: int
    problem: str
    official_solution: str
    topic: str | None = None
    final_answer: str | None = None
    notes: str | None = None


@dataclass
class Retrieved:
    entry: SolutionEntry
    exam: str
    course: str
    # How this match was made -- surfaced to the Grader/Reflector so weak grounding is
    # visible downstream, not only in the trace.
    method: str = ""
    score: float | None = None


@dataclass
class NotesChunk:
    source: str
    page: int
    text: str
    score: float


@dataclass
class Grade:
    id: str
    score: float
    max: int
    status: str  # "ok" | "partial" | "escalate"
    feedback: str
    justification: str
    confidence: float
    # GRADER_PROMPT v2 fields. All defaulted, so existing keyword constructors and the
    # `Grade(**{**grade.__dict__, ...})` splat in orchestrator.py keep working unchanged.
    question_id: str | None = None          # id the model read from the exam header, e.g. "Q2b"
    subscores: list[dict] = field(default_factory=list)  # [{"part","score","max"}], sums to score
    read_attempts: int = 1                  # 1|2|3 — reads the two-pass reading policy needed
    flags: list[str] = field(default_factory=list)       # borderline_*/missing_part/retrieval_weak
    sources: list[str] = field(default_factory=list)     # ids of solution/lecture chunks used


@dataclass
class Reflection:
    action: str  # "APPROVE" | "REVISE" | "ESCALATE"
    score: float | None
    feedback: str
    note: str
    confidence: float


@dataclass
class QuestionResult:
    id: str
    title: str
    score: float
    max: int
    status: str  # "ok" | "partial" | "escalate"
    mark: str    # short margin stamp, e.g. "✓" / "3/5"
    feedback: str
    # Reflection/aggregation annotations (e.g. "revised", "reflection_incomplete") --
    # surfaced so nothing the loop noticed is hidden from the final result.
    flags: list = field(default_factory=list)


def canonical_qid(q: "ParsedFragment", retrieved: "Retrieved | None") -> str:
    """The question's real identity for labelling: the KB id when retrieval matched it,
    else the Parser's read label as a last resort.

    `ParsedFragment.id` is only what the vision Parser read off the handwriting -- on a
    real booklet it comes back as "Q2b" for Q1b, as a bare "ב", or as the page-index
    fallback "Q{i+1}" that has nothing to do with the exam's numbering. It must never be
    the id shown in a prompt header or a trace entry: the model would be told it is
    grading one question while being handed another question's official solution.
    (Audit f4b64c4: 10 of 15 questions were labelled with an id that was not theirs, two
    of them with ids belonging to other real questions on the same exam.)"""
    return retrieved.entry.id if retrieved else (q.id or "")


@dataclass
class Step:
    """Brief-shaped trace entry: {module, prompt:{System_prompt,User_prompt}, response}."""
    module: str
    prompt: dict
    response: object
    pattern: str | None = None
