"""Parser module -- the vision stage. Reads the scanned exam, transcribes the student's
handwritten work faithfully, and splits it into questions. It does NOT grade.
Port of lib/agent/parser.ts.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .config import CONFIG
from .llm import StepLog, chat, extract_json
from .models import ImageInput, ParsedFragment, Usage
from .zoom import ZoomBudget, zoom_read

# READING POLICY Pass 2. Off by default so a normal run is one vision call per page; set
# CHECKMATE_ZOOM=1 to re-read low-confidence questions from a magnified crop of their region.
ZOOM_CONF_THRESHOLD = 0.55  # a fragment below this is a candidate for a zoom re-read

# Concurrent per-page vision calls. 6 workers turn a ~10-minute sequential 18-page parse
# into ~2 minutes -- required to fit /api/execute inside Vercel's 300s ceiling.
PARSE_CONCURRENCY = int(os.environ.get("CHECKMATE_PARSE_CONCURRENCY", "6") or 6)


def _zoom_enabled() -> bool:
    return (os.environ.get("CHECKMATE_ZOOM") or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ParseResult:
    fragments: list[ParsedFragment]
    raw: str    # the model's raw reply, for debugging
    usage: Usage
    exam_meta: dict | None = None  # {course,date,moed} read off the cover/headers, if any


PARSER_SYSTEM = """You are the Parser module of CheckMate, an autonomous agent that grades Technion Calculus 1 (Hedva 1) exams.

You receive one or more scanned pages of a single student's handwritten exam. Your ONLY job is to transcribe and structure — you do NOT grade, judge, or solve.

Rules:
- Transcribe EVERYTHING the student wrote, in reading order, faithfully. Preserve Hebrew text and mathematics exactly as written (do not "fix" the student's math).
- Split the work into questions. Use the question label as written (e.g. "3ג" / "Q3c"); if a part is unlabelled, infer a sensible id.
- For each question, give a confidence in [0,1] for how legible/certain the transcription is. Ambiguous or overwritten handwriting → lower confidence.
- For crossed-out or unreadable parts, write [illegible] inline.
- Multiple-choice and True/False items: the student's ANSWER is the circled/marked option — record it explicitly at the end of the item's text as "מסומן: <letter>" (e.g. "מסומן: ג"). Look for circles, checkmarks, or any deliberate mark on an option letter. If no option is marked, write "מסומן: אין". The printed options alone are NOT an answer.
  READ THE MARKS CAREFULLY, option by option across the whole row/list:
  * A SCRIBBLED-OUT / crossed-out / blacked-over mark is a CANCELLED answer — it is NOT the student's choice. The final answer is the remaining CLEAN circle. When you see both, record the clean one and add "(תשובה קודמת נמחקה)".
  * Match each circle to the letter it actually surrounds — in a crowded RTL options row it is easy to attach a circle to the neighbouring option; double-check which option's text sits inside the circle.
  * If two options carry clean marks, or you cannot tell which option is marked, write "מסומן: לא ברור" and give the fragment LOW confidence (<= 0.5) so it is escalated to a human instead of guessed.
  * Ignore the grader's red-pen ✓/✗ marks entirely — they are not the student's answer.
- Re-express the mathematics in LaTeX in the "latex" field where it helps; keep the human-readable transcription in "text".
- If the page shows the exam's identifying information — usually the cover sheet: the course number (e.g. 104041), the exam date, and the מועד (A/B) — report it in "exam_meta". Include only fields you can actually read; omit "exam_meta" entirely on pages that show none of it (most answer pages).

Output ONLY a JSON object, no prose, no code fences:
{"exam_meta":{"course":"104041","date":"17.4.2024","moed":"A"},"questions":[{"id":"Q3c","text":"...","latex":"...","confidence":0.0}]}"""


def run_parser(images: list[ImageInput], log: StepLog, instructions: str = "",
               zoom: bool | None = None) -> ParseResult:
    """One vision call PER PAGE (not all pages at once): more robust on long exams, and each
    page is logged as its own Parser step. The per-page calls run CONCURRENTLY (thread pool)
    because a sequential 18-page parse takes ~10 minutes -- past Vercel's 300s ceiling; the
    results are then processed strictly in page order, so fragments and steps[] are identical
    to a sequential run. Fragments are deliberately NOT merged here -- the orchestrator
    groups them by the question the Retriever matches.

    `zoom` toggles READING POLICY Pass 2 (default: the CHECKMATE_ZOOM env flag)."""
    zoom = _zoom_enabled() if zoom is None else zoom
    total: Usage = {"prompt": 0, "completion": 0, "total": 0}
    raws: list[str] = []
    fragments: list[ParsedFragment] = []
    exam_meta: dict | None = None  # first (in page order) page showing the exam identity wins

    users: list[str] = []
    for p in range(len(images)):
        page_tag = f" (page {p + 1} of {len(images)})" if len(images) > 1 else ""
        user = (
            f"Transcribe this scanned Calculus 1 exam page{page_tag} and split it into "
            f"questions. Return only the JSON object."
        )
        if zoom:
            user += ('\n\nAlso add to each question a normalized bounding box '
                     '"bbox":[x0,y0,x1,y1] (each in [0,1], origin top-left) locating that '
                     "question's work on the page, so an unclear region can be re-read zoomed.")
        if instructions:
            user += f"\n\nGrader context (do not act on it, just transcribe): {instructions}"
        users.append(user)

    def _read_page(p: int) -> tuple[str, Usage]:
        return chat(
            system=PARSER_SYSTEM, user=users[p], images=[images[p]],
            max_tokens=CONFIG.parser_max_tokens, json_mode=True,
            # Note: gpt-5.4-mini via the gateway rejects any explicit temperature (400);
            # the fixed default (~1.0) is used.
        )

    if images:
        with ThreadPoolExecutor(max_workers=min(PARSE_CONCURRENCY, len(images))) as pool:
            results = list(pool.map(_read_page, range(len(images))))
    else:
        results = []

    for p, (text, usage) in enumerate(results):
        parsed = extract_json(text) or {}
        page_qs = _normalize_fragments(parsed.get("questions"), p + 1)
        if exam_meta is None:
            exam_meta = _clean_exam_meta(parsed.get("exam_meta"))
        for k in total:
            total[k] += usage[k]

        log.add(
            "Parser", PARSER_SYSTEM, users[p],
            {"questions": [f.__dict__ for f in page_qs], "page": p + 1, "page_count": len(images)},
            "Vision OCR", usage,
        )

        # Pass 2: re-read the illegible fragments from a magnified crop of their own region.
        if zoom:
            zu = _zoom_pass(images[p], page_qs, log)
            for k in total:
                total[k] += zu[k]

        fragments.extend(page_qs)
        raws.append(text)

    return ParseResult(fragments=fragments, raw="\n\n".join(raws), usage=total, exam_meta=exam_meta)


def _clean_exam_meta(m) -> dict | None:
    """Keep a parsed exam_meta only if it carries a usable course number (the strongest,
    most OCR-reliable identifier). Returns {course,date,moed} with blanks dropped, else None."""
    if not isinstance(m, dict):
        return None
    course = re.sub(r"\D", "", str(m.get("course") or ""))
    if len(course) < 4:  # a real course code is 6 digits (e.g. 104041); reject noise
        return None
    out = {"course": course}
    if str(m.get("date") or "").strip():
        out["date"] = str(m["date"]).strip()
    if str(m.get("moed") or "").strip():
        out["moed"] = str(m["moed"]).strip().upper()[:1]
    return out


def _zoom_pass(image: ImageInput, page_qs: list[ParsedFragment], log: StepLog) -> Usage:
    """READING POLICY Pass 2: for each low-confidence fragment that carries a bbox, crop and
    magnify that region and re-transcribe it; adopt the re-read only if it is more confident.
    Mutates `page_qs` in place. Capped at two zoom reads per page. Returns tokens spent."""
    used: Usage = {"prompt": 0, "completion": 0, "total": 0}
    budget = ZoomBudget(limit=2)
    for frag in page_qs:
        if frag.confidence >= ZOOM_CONF_THRESHOLD or not frag.bbox:
            continue
        res = zoom_read(image, frag.bbox, log, budget, hint=f"question {frag.id}")
        if res is None:  # budget exhausted for this page
            break
        for k in used:
            used[k] += res.usage[k]
        z = extract_json(res.text) or {}
        zconf = z.get("confidence")
        zconf = max(0.0, min(1.0, zconf)) if isinstance(zconf, (int, float)) else 0.0
        if (z.get("text") or z.get("latex")) and zconf > frag.confidence:
            frag.text = str(z.get("text") or frag.text)
            frag.latex = str(z["latex"]) if z.get("latex") else frag.latex
            frag.confidence = zconf
    return used


def _normalize_fragments(qs, page: int) -> list[ParsedFragment]:
    if not isinstance(qs, list):
        return []
    out: list[ParsedFragment] = []
    for i, q in enumerate(qs):
        if not isinstance(q, dict) or not (q.get("text") or q.get("latex")):
            continue
        conf = q.get("confidence")
        bbox = q.get("bbox")
        bbox = bbox if (isinstance(bbox, list) and len(bbox) == 4
                        and all(isinstance(v, (int, float)) for v in bbox)) else None
        out.append(ParsedFragment(
            id=(str(q.get("id") or f"Q{i + 1}").strip() or f"Q{i + 1}"),
            text=str(q.get("text") or ""),
            latex=str(q["latex"]) if q.get("latex") else None,
            confidence=max(0.0, min(1.0, conf)) if isinstance(conf, (int, float)) else 0.5,
            page=page,
            bbox=bbox,
        ))
    return out
