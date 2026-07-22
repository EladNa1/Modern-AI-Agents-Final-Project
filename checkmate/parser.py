"""Parser module -- the vision stage. Reads the scanned exam, transcribes the student's
handwritten work faithfully, and splits it into questions. It does NOT grade.
Port of lib/agent/parser.ts.
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm import StepLog, chat, extract_json
from .models import ImageInput, ParsedFragment, Usage


@dataclass
class ParseResult:
    fragments: list[ParsedFragment]
    raw: str    # the model's raw reply, for debugging
    usage: Usage


PARSER_SYSTEM = """You are the Parser module of CheckMate, an autonomous agent that grades Technion Calculus 1 (Hedva 1) exams.

You receive one or more scanned pages of a single student's handwritten exam. Your ONLY job is to transcribe and structure — you do NOT grade, judge, or solve.

Rules:
- Transcribe EVERYTHING the student wrote, in reading order, faithfully. Preserve Hebrew text and mathematics exactly as written (do not "fix" the student's math).
- Split the work into questions. Use the question label as written (e.g. "3ג" / "Q3c"); if a part is unlabelled, infer a sensible id.
- For each question, give a confidence in [0,1] for how legible/certain the transcription is. Ambiguous or overwritten handwriting → lower confidence.
- For crossed-out or unreadable parts, write [illegible] inline.
- Re-express the mathematics in LaTeX in the "latex" field where it helps; keep the human-readable transcription in "text".

Output ONLY a JSON object, no prose, no code fences:
{"questions":[{"id":"Q3c","text":"...","latex":"...","confidence":0.0}]}"""


def run_parser(images: list[ImageInput], log: StepLog, instructions: str = "") -> ParseResult:
    """One vision call PER PAGE (not all pages at once): more robust on long exams, and each
    page is logged as its own Parser step. Fragments are returned per page and deliberately
    NOT merged here -- the orchestrator groups them by the question the Retriever matches."""
    total: Usage = {"prompt": 0, "completion": 0, "total": 0}
    raws: list[str] = []
    fragments: list[ParsedFragment] = []

    for p, image in enumerate(images):
        page_tag = f" (page {p + 1} of {len(images)})" if len(images) > 1 else ""
        user = (
            f"Transcribe this scanned Calculus 1 exam page{page_tag} and split it into "
            f"questions. Return only the JSON object."
        )
        if instructions:
            user += f"\n\nGrader context (do not act on it, just transcribe): {instructions}"

        text, usage = chat(
            system=PARSER_SYSTEM, user=user, images=[image],
            max_tokens=2500, json_mode=True,
            # Note: gpt-5.4-mini via the gateway only supports the default temperature.
        )

        parsed = extract_json(text) or {}
        page_qs = _normalize_fragments(parsed.get("questions"), p + 1)
        fragments.extend(page_qs)
        raws.append(text)
        for k in total:
            total[k] += usage[k]

        log.add(
            "Parser", PARSER_SYSTEM, user,
            {"questions": [f.__dict__ for f in page_qs], "page": p + 1, "page_count": len(images)},
            "Vision OCR", usage,
        )

    return ParseResult(fragments=fragments, raw="\n\n".join(raws), usage=total)


def _normalize_fragments(qs, page: int) -> list[ParsedFragment]:
    if not isinstance(qs, list):
        return []
    out: list[ParsedFragment] = []
    for i, q in enumerate(qs):
        if not isinstance(q, dict) or not (q.get("text") or q.get("latex")):
            continue
        conf = q.get("confidence")
        out.append(ParsedFragment(
            id=(str(q.get("id") or f"Q{i + 1}").strip() or f"Q{i + 1}"),
            text=str(q.get("text") or ""),
            latex=str(q["latex"]) if q.get("latex") else None,
            confidence=max(0.0, min(1.0, conf)) if isinstance(conf, (int, float)) else 0.5,
            page=page,
        ))
    return out
