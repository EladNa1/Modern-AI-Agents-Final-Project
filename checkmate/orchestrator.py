"""Orchestrator -- runs the CheckMate Reflection Agent end to end and assembles the
brief-shaped result. Port of lib/agent/orchestrator.ts.

Pipeline: Parser (once) -> per question: Retriever -> Grader -> Reflector (revise <= N) ->
approve|escalate. Falls back to the mock when there is no API key or no image to read.
"""
from __future__ import annotations

import re
import time
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
    t0 = time.time()
    log = StepLog()
    # Log the active tuning config first, so every trace/eval number is attributable to it.
    # Resolved runtime identities included (secret-free) so the production configuration is
    # verifiable from the trace alone: which models actually answered, which vector backend.
    from .env import HAS_PINECONE, LLMOD_EMBED_MODEL, LLMOD_GRADER_MODEL, LLMOD_MODEL, PINECONE_INDEX
    log.add("Config", "Active tuning config for this run (section 6).", "",
            {**CONFIG.to_log(),
             "resolved_models": {"chat": LLMOD_MODEL, "grader": LLMOD_GRADER_MODEL,
                                 "embedding": LLMOD_EMBED_MODEL, "embedding_dim": 1536},
             "vector_backend": (f"pinecone:{PINECONE_INDEX}" if HAS_PINECONE
                                else "bundled-json (no vector db configured)")},
            "Config")

    try:
        # `parsed` supplied -> reuse a cached transcription (6.1: never re-run vision on a
        # booklet already parsed; re-parse only when the parser itself changes).
        if parsed is None:
            parsed = run_parser(images, log, instructions,
                                deadline=t0 + CONFIG.parse_deadline_seconds)
        else:
            log.add("Parser", "Cached transcription reused (no vision call).", "",
                    {"cached": True, "fragments": len(parsed.fragments),
                     "exam_meta": parsed.exam_meta}, "Cache")
        if not parsed.fragments:
            # Say WHICH failure this was: an unreadable scan and a parse that ran out of
            # wall-clock need different advice, and blaming the image for our own timeout
            # would send the user off to rescan a perfectly good booklet.
            if getattr(parsed, "skipped_pages", None):
                err = (f"The parse deadline ({CONFIG.parse_deadline_seconds}s) passed before any "
                       "page could be read, so nothing was graded. Try a shorter PDF, or raise "
                       "CHECKMATE_PARSE_DEADLINE.")
            else:
                err = "The Parser could not read any question from the scan. Try a clearer image."
            return {"status": "error", "error": err, "response": None, "steps": _steps(log)}

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
            # Projected ceiling check (6.3), BEFORE spending: if grading the next question
            # at the run's average cost/time so far would cross a ceiling, stop now and
            # return a provisional result -- never discover the overrun after the money is
            # spent. (The post-question check below still catches an unusually large jump.)
            # Absolute check first, and NOT gated on having finished a question: a long
            # parse can burn the whole budget before question 1, and the projection below
            # cannot run with no completed question to average over.
            if time.time() - t0 > CONFIG.max_run_seconds:
                aborted = True
                break
            done = len(results)
            if done:
                spent = log.cost_by_stage()["total"]
                elapsed = time.time() - t0
                if (spent + spent / done > CONFIG.max_run_cost_usd
                        or elapsed + elapsed / done > CONFIG.max_run_seconds):
                    aborted = True
                    break
            merged = _merge_fragments(group["fragments"])
            q = merged if group["retrieved"] else ParsedFragment(
                id=UNMATCHED, text=merged.text, latex=merged.latex,
                confidence=merged.confidence, page=merged.page)
            retrieved = group["retrieved"]
            qid = retrieved.entry.id if retrieved else q.id

            # Unmatched bucket: writing that no KB question claimed with confidence (e.g. a
            # continuation page without a printed header, demoted by the retrieval
            # corroboration check). Grading it would spend tokens to fabricate a 0/0 --
            # route it straight to the human with an honest note instead.
            if retrieved is None and q.id == UNMATCHED:
                grade = Grade(
                    id=UNMATCHED, score=0, max=0, status="escalate",
                    feedback="Writing that could not be confidently matched to any exam "
                             "question (typically a continuation page without a printed "
                             "header). Forwarded to the teacher for manual placement — it "
                             "carries no points and does not affect the total.",
                    justification="No knowledge-base question claimed these fragments with "
                                  "sufficient confidence; not graded automatically.",
                    confidence=0.0)
                log.add("Router",
                        "Route unmatched writing to the human reviewer instead of grading "
                        "it against a guessed question. No LLM call.",
                        f"unmatched fragments={len(group['fragments'])}",
                        {"decision": "ESCALATE_UNMATCHED", "llm_calls": 0}, "Scope")
                results.append(_to_question_result(q.id, grade, None))
                gb.add(qid, entry_from_grade(grade, None))
                continue

            query = f"{q.text} {q.latex or ''}"
            notes = retrieve_notes(query, log)
            grade = run_grader(q, retrieved, log, notes,
                               deadline=t0 + CONFIG.max_run_seconds)

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
                # REVISE -- canonical Reflection loop (Self-Refine/Reflexion, as taught):
                # the critique goes BACK TO THE GENERATOR, which regrades conditioned on
                # it (and may keep its grade if the critique is unjustified). The revised
                # grade is re-critiqued on the next pass. An escalation is still never
                # cleared: a revision regrade cannot downgrade an escalate (guarded below).
                was_escalated = grade.status == "escalate"
                crit_text = (refl.feedback or refl.note or "").strip() or "(no critique text)"
                before_rev = log.usage["total"]
                regrade = run_grader(q, retrieved, log, notes, critique=(grade, crit_text),
                                     deadline=t0 + CONFIG.max_run_seconds)
                refl_tokens += log.usage["total"] - before_rev
                if was_escalated and regrade.status != "escalate":
                    regrade = Grade(**{**regrade.__dict__, "status": "escalate"})
                grade = regrade

            results.append(_to_question_result(q.id, grade, retrieved))
            gb.add(qid, entry_from_grade(grade, retrieved))

            # Run ceilings (6.3): stop before the next question past the budget ceiling OR
            # the wall-clock guard (Vercel kills the whole request at 300s -- better to
            # return a provisional result than nothing).
            if (log.cost_by_stage()["total"] > CONFIG.max_run_cost_usd
                    or time.time() - t0 > CONFIG.max_run_seconds):
                aborted = True
                break

        gb.cost = log.cost_by_stage()
        # Deterministic assembly step -- no LLM call; logged so the trace shows how the
        # final result is built (completion judged arithmetically from the manifest).
        report = gb.final_report()
        log.add("GradeBook",
                "Deterministic assembly (no LLM call): accumulate per-question entries and "
                "decide completion arithmetically from the exam manifest -- a total is never "
                "reported over missing questions without saying so.",
                f"questions={len(results)}, exam={exam}",
                {"booklet_status": gb.status(), "llm_calls": 0,
                 "missing_questions": (report or {}).get("report", {}).get("missing_questions")
                 if isinstance(report, dict) else None},
                "Assembly")
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


def _to_question_result(qid: str, grade: Grade, retrieved: Retrieved | None) -> QuestionResult:
    # Prefer the KB's canonical id -- the Parser's guess is unreliable when the scan shows
    # only one part (e.g. just "(ג)").
    display_id = retrieved.entry.id if retrieved else qid
    topic = retrieved.entry.topic if retrieved else None
    title = f"{display_id} · {topic}" if topic else display_id
    mark = "✓" if grade.status == "ok" else ("?" if grade.status == "escalate" else f"{grade.score}/{grade.max}")
    return QuestionResult(id=display_id, title=title, score=grade.score, max=grade.max,
                          status=grade.status, mark=mark, feedback=grade.feedback,
                          flags=list(grade.flags))


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
        *[f"{q.id} — {q.score}/{q.max}"
          + (f" [{', '.join(q.flags)}]" if q.flags else "")
          + f": {q.feedback}" for q in questions], "",
        (f"⚠ {len(escalated)} question(s) escalated to the teacher: "
         f"{', '.join(q.id for q in escalated)} — their {sum(q.score for q in escalated):g} "
         "auto-scored point(s) are PROVISIONAL pending human review; the total may change."
         ) if escalated else "No questions required human review.",
        (f"⚠ Run stopped early at a ceiling (${CONFIG.max_run_cost_usd:.2f} budget / "
         f"{CONFIG.max_run_seconds}s wall-clock) — remaining questions were not graded.") if aborted else "",
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
