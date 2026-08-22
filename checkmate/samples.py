"""Bundled sample booklets for the JSON /api/execute contract.

The course spec's required entry point is a TEXT prompt -- but the agent's real input is a
scanned booklet. Bridge: we bundle the cached vision transcription of real graded booklets
(parsed once, offline -- per the course guidance that vision OCR need not re-run per demo),
and a prompt that names one of them runs the REAL pipeline -- Router -> Retriever -> Grader
-> Reflector -> GradeBook -- with live LLM calls and real autonomous decisions. Only the
vision stage is cached; nothing else is canned.

Registry ids double as the names users/graders type, e.g.
  {"prompt": "Grade sample booklet 1"}  or  {"prompt": "Grade the 2024 winter moed A exam"}.
"""
from __future__ import annotations

import json
import os
import re

_DIR = os.path.join(os.path.dirname(__file__), "kb", "samples")

# id -> bundled cached-parse file + the exam that scopes retrieval + alias patterns.
SAMPLES: list[dict] = [
    {
        "id": "sample-1",
        "label": "Sample booklet 1 — 104041 2024 Winter Moed A (18-page scan, human-graded 90/100)",
        "file": "104041-2024W-A.json",
        "exam": "2024w moed A",
        # THE authoritative human mark for this booklet -- the one number every surface
        # (GUI button, result card, replay) quotes, so the teacher's grade can never appear
        # as two different figures depending on where you read it.
        "human_total": 90,
        # A paid grading run starts ONLY from an explicit booklet name (or an uploaded
        # file). Generic exam terms ("104041", "moed a") used to alias to this booklet and
        # silently started a real run the caller never asked for -- e.g. a solve request
        # that mentioned the course number (audit round 11). Those now fall through to the
        # zero-cost NEED_INPUT explainer, which lists the samples by name.
        "specific": r"sample\s*(?:booklet\s*)?(?:#\s*)?1\b|booklet\s*1\b|first\s+(?:sample|booklet)|scored\s*90|\b90/100",
        "aliases": r"(?!x)x",  # no generic aliases -- reachable only by name
        # Rendered page previews bundled under static/ so the GUI can SHOW what was graded.
        "pages": 18,
        "pages_prefix": "/static/samples/sample1",
    },
    {
        "id": "sample-2",
        "label": "Sample booklet 2 — 104041 2024 Winter Moed A, second student (18-page scan, human-graded 93/100)",
        "file": "104041-2024W-A-93.json",
        "exam": "2024w moed A",
        "human_total": 93,
        "specific": r"sample\s*(?:booklet\s*)?(?:#\s*)?2\b|booklet\s*2\b|second\s+(?:sample|booklet|student)|scored\s*93|\b93/100",
        "aliases": r"(?!x)x",  # no generic aliases -- reachable only by name
        "pages": 18,
        "pages_prefix": "/static/samples/sample2",
    },
]


def list_samples() -> list[dict]:
    return [{"id": s["id"], "label": s["label"], "exam": s["exam"],
             "human_total": s.get("human_total")} for s in SAMPLES]


def resolve_sample(prompt: str) -> dict | None:
    """Match a prompt to a bundled sample booklet. Explicit booklet names win over generic
    exam aliases across ALL samples (two passes), so naming booklet 2 alongside a generic
    term like the course number can never resolve to booklet 1."""
    p = prompt or ""
    for key in ("specific", "aliases"):
        for s in SAMPLES:
            if re.search(s[key], p, re.IGNORECASE) and os.path.exists(os.path.join(_DIR, s["file"])):
                return s
    return None


def load_sample_parse(sample: dict):
    """Rebuild a ParseResult from the bundled cached transcription (same format as
    eval/ocr_cache). No parser-version check -- the bundle is pinned deliberately."""
    from .models import ParsedFragment
    from .parser import ParseResult

    d = json.load(open(os.path.join(_DIR, sample["file"]), encoding="utf-8"))
    frags = [ParsedFragment(**f) for f in d["fragments"]]
    return ParseResult(fragments=frags, raw=d.get("raw", ""),
                       usage={"prompt": 0, "completion": 0, "total": 0},
                       exam_meta=d.get("exam_meta"))


def available_samples_text() -> str:
    """Helpful in-scope response when no sample was named and no file was attached."""
    lines = [
        # Deliberately NON-committal about the request's domain: this branch is reachable by
        # any prompt containing an exam noun ("grade my sourdough bread baking exam"), and
        # asserting "that looks like a grading request" about bread was wrong (audit round
        # 13). State what CheckMate does and what it needs; claim nothing about the ask.
        "CheckMate grades scanned Technion Calculus 1 (Hedva 1) exam booklets. No scan is "
        "attached and no bundled booklet was named, so there is nothing to grade.",
        "",
        "To grade a booklet: upload the scanned exam (multipart `file` on /api/execute, or "
        "the upload box in the UI), or name one of the bundled sample booklets in your "
        "prompt:",
        *[f'  - "{s["id"]}" — {s["label"]}' for s in SAMPLES],
        "",
        'Example: {"prompt": "Grade sample booklet 1"}',
        "",
        "(If you were asking about something other than grading a Calculus 1 exam, that is "
        "outside CheckMate's scope.)",
    ]
    return "\n".join(lines)
