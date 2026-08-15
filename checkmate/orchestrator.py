"""Orchestrator -- runs the CheckMate Reflection Agent end to end and assembles the
brief-shaped result. Port of lib/agent/orchestrator.ts.

Pipeline: Parser (once) -> per question: Retriever -> Grader -> Reflector (revise <= N) ->
approve|escalate. Falls back to the mock when there is no API key or no image to read.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict

from .config import CONFIG
from .env import HAS_LLM
from .grader import _is_tf_mc, run_grader
from .gradebook import GradeBook, entry_from_grade
from .kb.exams import manifest_for, match_exam
from .llm import StepLog
from .mock_agent import run_mock_agent
from .models import Grade, ImageInput, ParsedFragment, QuestionResult, Retrieved
from .parser import run_parser
from .reflector import run_reflector
from .retriever import retrieve, retrieve_notes

MAX_REVISE_PASSES = CONFIG.max_revise_passes
UNMATCHED = "Unmatched work"  # bucket for transcribed work that matches no KB question


def run_agent(images: list[ImageInput], instructions: str = "", source_label: str = "",
              exam: str | None = None, parsed=None) -> dict:
    # Fallback: no key configured or nothing to look at -> deterministic mock.
    if not HAS_LLM or (not images and parsed is None):
        return run_mock_agent(source_label, instructions or "")

    instructions = (instructions or "").strip()
    log = StepLog()
    # Log the active tuning config first, so every trace/eval number is attributable to it.
    log.add("Config", "Active tuning config for this run (section 6).", "",
            CONFIG.to_log(), "Config")

    try:
        # `parsed` supplied -> reuse a cached transcription (6.1: never re-run vision on a
        # booklet already parsed; re-parse only when the parser itself changes).
        if parsed is None:
            parsed = run_parser(images, log, instructions)
        else:
            log.add("Parser", "Cached transcription reused (no vision call).", "",
                    {"cached": True, "fragments": len(parsed.fragments),
                     "exam_meta": parsed.exam_meta}, "Cache")
        if not parsed.fragments:
            return {"status": "error",
                    "error": "The Parser could not read any question from the scan. Try a clearer image.",
                    "response": None, "steps": _steps(log)}

        # Resolve which exam to scope retrieval to. A caller-provided exam wins (manual
        # override from the UI); otherwise auto-detect it from the exam identity the Parser
        # read off the scan (cover/headers); if neither is available the run stays unscoped
        # and the consistency guard keeps a single booklet coherent.
        exam_source = "manual" if exam else "none"
        if not exam and parsed.exam_meta:
            detected = match_exam(course=parsed.exam_meta.get("course"),
                                  year=_year_from(parsed.exam_meta.get("date")),
                                  moed=parsed.exam_meta.get("moed"))
            if detected:
                exam, exam_source = detected, "auto"
        log.add("Router", "Decide which exam to scope retrieval to (manual override, else "
                "auto-detected from the scan, else unscoped).",
                f"exam_meta={parsed.exam_meta}",
                {"exam": exam, "source": exam_source}, "Scope")

        # Drop booklet boilerplate (cover / instructions / blank pages) BEFORE retrieval --
        # it is not student work, and grading it would only emit a confusing "Unmatched
        # work" escalation. Deterministic, zero LLM; the decision is logged.
        kept = [f for f in parsed.fragments if not _is_boilerplate(f)]
        dropped = [f.id for f in parsed.fragments if _is_boilerplate(f)]
        if dropped:
            log.add("Router", "Deterministic pre-filter: exclude non-answer booklet "
                    "boilerplate (cover, instructions, blank pages) from grading.",
                    f"fragments={len(parsed.fragments)}",
                    {"dropped": dropped, "kept": len(kept), "llm_calls": 0}, "Scope")

        # Group the transcribed fragments by the exam question they actually answer -- the
        # Retriever matches each fragment on CONTENT, and that match defines a question here.
        fragments = _split_tf_fragments(kept, log)
        groups = _group_by_retrieved_question(fragments, log, exam)

        # Domain guard: no exam identity on the scan AND nothing matched the knowledge base
        # -> this is not a Calculus 1 exam we know. Refuse politely INSTEAD of grading --
        # spending grader/reflector tokens on out-of-domain content would only fabricate
        # scores (the steps so far show the decision trail: parse -> retrieve -> refuse).
        if not exam and not any(g["retrieved"] for g in groups):
            log.add("Router",
                    "Final scope check: is there any evidence this scan is a Calculus 1 exam "
                    "(exam identity on the cover, or content matching the knowledge base)?",
                    f"fragments={len(parsed.fragments)}, kb_matches=0, exam_meta={parsed.exam_meta}",
                    {"in_scope": False, "decision": "REFUSE",
                     "reason": "No exam identity detected and no fragment matched any known "
                               "exam question -- refusing to grade out-of-domain content."},
                    "Scope")
            return {
                "status": "ok", "error": None,
                "response": (
                    "This upload doesn't look like a Technion Calculus 1 exam I can grade: no "
                    "exam identity was found on the scan, and none of its content matched any "
                    "question in the course knowledge base.\n\nCheckMate only grades Calculus 1 "
                    "(Hedva 1) exam booklets. If this IS such an exam, try a clearer scan of the "
                    "full booklet (including the cover), or pick the exam manually and re-run."),
                "steps": _steps(log),
                "meta": {"mode": "refused", "source": source_label, "total": 0, "max": 0,
                         "questions": [], "cost": log.cost_by_stage()},
            }

        # GradeBook accumulates per-question entries; completion is judged against the KB
        # manifest (arithmetic), never the model -- so a total is never built on missing slots.
        gb = GradeBook(booklet_id=source_label or "booklet", exam_id=exam,
                       config_snapshot=CONFIG.to_log(), manifest=manifest_for(exam) if exam else None)

        results: list[QuestionResult] = []
        aborted = False
        for group in groups:
            merged = _merge_fragments(group["fragments"])
            q = merged if group["retrieved"] else ParsedFragment(
                id=UNMATCHED, text=merged.text, latex=merged.latex,
                confidence=merged.confidence, page=merged.page)
            retrieved = group["retrieved"]
            qid = retrieved.entry.id if retrieved else q.id
            query = f"{q.text} {q.latex or ''}"
            notes = retrieve_notes(query, log)
            grade = run_grader(q, retrieved, log, notes)

            # Reflection loop (7.3): passes allocated by stakes, T/F+MC skipped (no argument to
            # critique), and a cumulative per-question token budget that exits early -- a grade
            # that ran out of review budget is flagged, not silently accepted.
            passes = _reflection_passes(qid, grade.max)
            refl_tokens = 0
            for _ in range(passes):
                if refl_tokens >= CONFIG.max_reflection_tokens_per_q:
                    if "reflection_incomplete" not in grade.flags:
                        grade.flags.append("reflection_incomplete")
                    break
                before = log.usage["total"]
                refl = run_reflector(q, grade, retrieved, log)
                refl_tokens += log.usage["total"] - before
                if refl.action == "APPROVE":
                    break
                if refl.action == "ESCALATE":
                    grade = Grade(**{**grade.__dict__, "status": "escalate"})
                    break
                grade = _apply_revision(grade, refl.score, refl.feedback)  # REVISE

            results.append(_to_question_result(q.id, grade, retrieved))
            gb.add(qid, entry_from_grade(grade, retrieved))

            # Budget guardrail (6.3): stop before the next question if we've hit the ceiling.
            if log.cost_by_stage()["total"] > CONFIG.max_run_cost_usd:
                aborted = True
                break

        gb.cost = log.cost_by_stage()
        return _assemble(results, source_label, instructions, log, exam, exam_source, aborted, gb)
    except Exception as err:
        # Any gateway/parse failure -> a valid error payload (never raise to the route).
        return {"status": "error", "error": "The agent failed while grading: " + str(err),
                "response": None, "steps": _steps(log)}


# Booklet boilerplate the Parser faithfully transcribes but which is NOT student work:
# the cover sheet, the instructions page, blank pages, the "good luck" tail. Grading it
# would only produce a confusing "Unmatched work" escalation (and waste retrieval calls).
_BOILERPLATE_RE = re.compile(
    r"\[blank page\]|דף ריק|מחברת בחינה|תעודת הזהות|לתשומת לבך|ציונים לשימוש הבוחן|"
    r"אין לעזוב|אין לתלוש|מכון טכנולוגי לישראל|משך הבחינה|בהצלחה\s*!*\s*$")


def _is_boilerplate(f: ParsedFragment) -> bool:
    text = (f.text or "").strip()
    if not text:
        return True
    if not _BOILERPLATE_RE.search(text):
        return False
    # Boilerplate keyword present -- treat as boilerplate only if there is no real math
    # content riding along (a student may write work under a printed header). Note: a bare
    # backslash is NOT math -- the parser wraps plain Hebrew in \text{...}.
    combined = text + " " + (f.latex or "")
    mathy = re.search(r"\\(frac|int|sum|lim|sqrt|left|right)\b|[∫∑√≤≥]|\b(sin|cos|tan|ln|lim)\b",
                      combined)
    return not mathy


# OCR renders the T/F header loosely ("סמנו נכון/לא נכון", "סמנו/לא נכון", ...) -- the
# stable signal is "לא נכון" (or an explicit true/false) plus multiple numbered items.
_TF_HEADER_RE = re.compile(r"לא\s*נכון|נכון\s*/|true\s*/?\s*false", re.IGNORECASE)
_TF_ITEM_RE = re.compile(r"(?m)^\s*([1-9])\s*[.)]\s")


def _split_tf_fragments(fragments: list[ParsedFragment], log: StepLog) -> list[ParsedFragment]:
    """The parser sometimes transcribes the whole True/False page as ONE fragment (numbered
    statements + the student's circled answers). One fragment can only content-match ONE KB
    entry, so its siblings would go missing from the GradeBook. Split it deterministically
    (zero LLM) into per-item fragments with canonical TF-n ids that match the KB exactly."""
    out: list[ParsedFragment] = []
    for f in fragments:
        text = f.text or ""
        marks = list(_TF_ITEM_RE.finditer(text))
        if not (_TF_HEADER_RE.search(text) and len(marks) >= 2):
            out.append(f)
            continue
        header = text[:marks[0].start()].strip()
        pieces = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            item_no = m.group(1)
            body = text[m.start():end].strip()
            pieces.append(ParsedFragment(
                id=f"TF-{item_no}",
                text=(header + "\n" + body) if header else body,
                latex=None, confidence=f.confidence, page=f.page))
        out.extend(pieces)
        log.add("Parser", "Deterministic post-pass: split a merged True/False page into "
                "per-item fragments so each statement matches its own KB entry (no LLM call).",
                f"fragment id={f.id!r} page={f.page}",
                {"split_into": [p.id for p in pieces], "llm_calls": 0}, "Post-process")
    return out


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


def _reflection_passes(qid: str, max_points: int) -> int:
    """How many reflection passes this question gets (7.3): none for T/F+MC (nothing to
    critique), full budget for high-stakes open questions, one pass for smaller ones."""
    if _is_tf_mc(qid) and not CONFIG.reflect_tf_mc:
        return 0
    return CONFIG.max_revise_passes if max_points >= CONFIG.reflection_high_threshold \
        else CONFIG.reflection_passes_open


def _apply_revision(grade: Grade, new_score: float | None, new_feedback: str) -> Grade:
    score = max(0.0, min(grade.max, new_score)) if new_score is not None else grade.score
    # A REVISE may correct the score/feedback, but it must NEVER downgrade an escalation:
    # once the grade is escalated (self-consistency `grader_disagreement`, or a missing/E3
    # key), the reflector -- the same weak model -- cannot silently mark it graded. Keeping
    # the escalate preserves the safety net (and the flags that ride with it).
    if grade.status == "escalate":
        status = "escalate"
    else:
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


def _year_from(date) -> str | None:
    """Pull a 4-digit year out of a detected date string (e.g. '17.4.2024' -> '2024')."""
    m = re.search(r"\d{4}", str(date or ""))
    return m.group(0) if m else None


def _assemble(questions: list[QuestionResult], source: str, instructions: str, log: StepLog,
              exam: str | None = None, exam_source: str = "none", aborted: bool = False,
              gradebook: "GradeBook | None" = None) -> dict:
    total = sum(q.score for q in questions)
    max_pts = sum(q.max for q in questions)
    escalated = [q for q in questions if q.status == "escalate"]
    cost = log.cost_by_stage()

    scope_line = (f"Graded as {exam} ({'auto-detected' if exam_source == 'auto' else 'selected'})."
                  if exam else "Exam not identified — graded unscoped; pick the exam to re-grade more accurately.")
    lines = [
        f"CheckMate graded {', '.join(q.id for q in questions)}: {total} / {max_pts}.",
        scope_line, "",
        *[f"{q.id} — {q.score}/{q.max}: {q.feedback}" for q in questions], "",
        (f"⚠ {len(escalated)} question(s) escalated to the teacher: "
         f"{', '.join(q.id for q in escalated)}.") if escalated else "No questions required human review.",
        (f"⚠ Run stopped early at the ${CONFIG.max_run_cost_usd:.2f} budget ceiling — "
         "remaining questions were not graded.") if aborted else "",
        f"Estimated cost: ${cost['total']:.4f}.",
    ]

    return {
        "status": "ok", "error": None, "response": "\n".join(p for p in lines if p != ""),
        "steps": _steps(log),
        "meta": {"total": total, "max": max_pts, "questions": [asdict(q) for q in questions],
                 "source": source, "mode": "full", "instructions": instructions,
                 "exam": exam, "exam_source": exam_source, "aborted": aborted, "cost": cost,
                 "gradebook": gradebook.final_report() if gradebook else None,
                 "booklet_status": gradebook.status() if gradebook else None},
    }


def _steps(log: StepLog) -> list[dict]:
    return [asdict(s) for s in log.steps]
