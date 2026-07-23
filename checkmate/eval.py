"""Eval harness -- measures CheckMate against human red-pen ground truth.

Ground truth lives in `eval/graded/*.json` (one per graded booklet): per-question human
scores + the cover total. Two modes, because grading costs budget but routing does not:

  python -m checkmate.eval          # FREE: routing accuracy + report skeleton, no LLM calls
  python -m checkmate.eval --live   # runs the full agent on each booklet (COSTS budget) and
                                    # scores open-question MAE, T/F+MC exact-match, false
                                    # zeros, and escalations against the human ground truth

Writes a JSON report to `eval/reports/` and prints a one-screen scorecard.

Corpus note: today this is ~1-2 booklets, so the numbers are a regression tripwire, not a
population estimate. Split discipline (`split: eval` vs `fewshot`) is honoured so a booklet
whose graded answers sit in the prompt is never scored on itself.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import statistics
import sys
import time

from .kb.exams import match_exam

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GRADED = os.path.join(_ROOT, "eval", "graded")
_REPORTS = os.path.join(_ROOT, "eval", "reports")
_OCR = os.path.join(_ROOT, "eval", "ocr_cache")
_DATA = os.path.join(_ROOT, "Data")

# Graded student booklet filenames encode the human total as a suffix, e.g.
# "104041_2024_Winter_A_90.pdf" -> course 104041, winter 2024, moed A, human total 90.
_BOOKLET_RE = re.compile(r"(?P<course>\d{6})[_ ](?P<year>\d{4})[_ ](?P<term>Winter|Spring|Summer)"
                         r"[_ ](?P<moed>[A-Z])[_ ](?P<score>\d{2,3})\.pdf$", re.IGNORECASE)
_TERM = {"winter": "w", "spring": "s", "summer": "s"}


def _year_from(date) -> str | None:
    import re
    m = re.search(r"\d{4}", str(date or ""))
    return m.group(0) if m else None


def load_ground_truth() -> list[dict]:
    return [json.load(open(f, encoding="utf-8"))
            for f in sorted(glob.glob(os.path.join(_GRADED, "*.json")))]


def discover_booklets() -> list[dict]:
    """Every graded student booklet under Data/ (human total in the filename). None of the
    deterministic metrics below need a KB or official solution, so this spans the WHOLE
    corpus -- not just the exams we happen to have solutions for."""
    out = []
    for p in glob.glob(os.path.join(_DATA, "**", "*.pdf"), recursive=True):
        m = _BOOKLET_RE.search(os.path.basename(p))
        if not m:
            continue
        out.append({"path": p, "rel": os.path.relpath(p, _ROOT),
                    "course": m.group("course"), "year": m.group("year"),
                    "term": _TERM.get(m.group("term").lower(), ""),
                    "moed": m.group("moed").upper(), "human_total": int(m.group("score"))})
    return sorted(out, key=lambda b: (b["course"], b["year"], b["moed"]))


def _is_open(qid: str) -> bool:
    """Open (partial-credit) question vs all-or-nothing T/F / MC."""
    return not (qid.upper().startswith("MC") or qid.upper().startswith("TF"))


def routing_accuracy(gts: list[dict]) -> dict:
    """FREE: does auto-detect resolve each booklet's exam_meta to the right exam label?"""
    rows, hits = [], 0
    for gt in gts:
        em = gt.get("exam_meta") or {}
        pred = match_exam(course=em.get("course"), year=_year_from(em.get("date")),
                          moed=em.get("moed"))
        ok = pred == gt.get("exam_label")
        hits += int(ok)
        rows.append({"exam_id": gt["exam_id"], "predicted": pred,
                     "true": gt.get("exam_label"), "ok": ok})
    return {"accuracy": (hits / len(gts)) if gts else None, "n": len(gts), "rows": rows}


def ink_pass(booklets: list[dict], max_pages: int = 20) -> dict:
    """FREE (zero LLM): ink statistics for every graded booklet. Establishes the
    "region contains ink" side of the false-zero metric across the WHOLE corpus. The
    inked-vs-empty-transcript JOIN needs cached parser transcripts (one OCR pass per
    booklet) -- reported separately once that cache exists."""
    from .ink import booklet_ink_stats
    rows = []
    for b in booklets:
        with open(b["path"], "rb") as f:
            pages = booklet_ink_stats(f.read(), max_pages=max_pages)
        rows.append({
            "booklet": os.path.basename(b["path"]), "course": b["course"],
            "human_total": b["human_total"], "pages": len(pages),
            "inked_cells": sum(p["inked_cells"] for p in pages),
            "red_pages": sum(1 for p in pages if p["has_red"]),
            "avg_student_frac": round(statistics.mean([p["student_frac"] for p in pages]), 4) if pages else 0.0,
        })
    return {"n": len(rows), "rows": rows}


def _parser_version() -> str:
    """Fingerprint of the parser stage; a change here (and only here) invalidates the OCR
    cache, per rule 6.1."""
    src = open(os.path.join(os.path.dirname(__file__), "parser.py"), encoding="utf-8").read()
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]


def _load_cached_parse(booklet_id: str, version: str):
    path = os.path.join(_OCR, booklet_id + ".json")
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding="utf-8"))
    if d.get("parser_version") != version:
        return None  # parser changed -> stale, must re-parse
    from .models import ParsedFragment
    from .parser import ParseResult
    frags = [ParsedFragment(**f) for f in d["fragments"]]
    return ParseResult(fragments=frags, raw=d.get("raw", ""),
                       usage={"prompt": 0, "completion": 0, "total": 0}, exam_meta=d.get("exam_meta"))


def _save_cached_parse(booklet_id: str, version: str, parsed) -> None:
    os.makedirs(_OCR, exist_ok=True)
    json.dump({"parser_version": version, "exam_meta": parsed.exam_meta, "raw": parsed.raw,
               "fragments": [f.__dict__ for f in parsed.fragments]},
              open(os.path.join(_OCR, booklet_id + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def grade_booklet_live(gt: dict) -> tuple[dict[str, dict], dict]:
    """Grade one booklet. Vision (parse) runs ONCE and is cached to disk (6.1); on later runs
    where only the grader/reflector/config changed, the parse is reused for free. Returns
    ({question_id: result}, cost_by_stage)."""
    from .llm import StepLog
    from .orchestrator import run_agent
    from .parser import run_parser
    from .pdf import render_pdf_to_images

    bid, version = gt["exam_id"], _parser_version()
    parsed = _load_cached_parse(bid, version)
    parse_cost = 0.0
    if parsed is None:  # cache miss -> pay for vision once, then persist
        with open(os.path.join(_ROOT, gt["booklet"]), "rb") as f:
            render = render_pdf_to_images(f.read(), max_pages=60)
        plog = StepLog()
        parsed = run_parser(render.images, plog)
        parse_cost = plog.cost_by_stage()["total"]
        _save_cached_parse(bid, version, parsed)

    res = run_agent([], "", bid, exam=gt.get("exam_label"), parsed=parsed)
    meta = res.get("meta", {})
    got = {q["id"]: q for q in meta.get("questions", [])}
    cost = dict(meta.get("cost", {}))
    cost["Parser"] = round(cost.get("Parser", 0) + parse_cost, 4)  # add the (cached-once) parse
    cost["total"] = round(cost.get("total", 0) + parse_cost, 4)
    cost["parse_cached"] = parse_cost == 0.0
    return got, cost


def score_grading(gt: dict, got: dict[str, dict]) -> dict:
    """Compare model results to human ground truth for one booklet."""
    open_abs: list[float] = []
    tf_mc_hits = tf_mc_total = false_zeros = escalations = 0
    false_deducts = full_credit_items = false_deduct_points = 0
    esc_tp = esc_fp = esc_fn = 0  # escalation precision/recall bookkeeping
    per_q = []
    for qid, truth in gt["questions"].items():
        g = got.get(qid)
        model = g["score"] if g else None
        status = g["status"] if g else "missing"
        human = truth["human"]
        disputed = bool(truth.get("disputed"))  # bad human key -> don't measure against it
        # "wrong" = a shippable grade that misses the human by > tolerance (2 pts open;
        # any mismatch on all-or-nothing T/F+MC). Escalation should catch exactly these.
        tol = 2 if _is_open(qid) else 0
        wrong = (not disputed) and model is not None and abs(model - human) > tol
        escalated = bool(g) and status == "escalate"
        if escalated:
            escalations += 1
            esc_tp += int(wrong)
            esc_fp += int(not wrong)
        elif wrong:
            esc_fn += 1  # shipped a wrong grade without flagging -- the dangerous miss
        if _is_open(qid):
            if model is not None and not disputed:
                open_abs.append(abs(model - human))
        elif not disputed:
            tf_mc_total += 1
            tf_mc_hits += int(model is not None and model == human)
        # false zero: human gave credit, model said 0 and claimed no work
        if human > 0 and g and model == 0 and "no work" in (g.get("feedback", "").lower()):
            false_zeros += 1
        # false deduction: human gave FULL marks ("do not deduct"), model took some off.
        # Track both RATE and SEVERITY (points wrongly removed) -- 1/12 hides "lost 5 on Q1a".
        if human == truth["max"] and not disputed:
            full_credit_items += 1
            if model is not None and model < human:
                false_deducts += 1
                false_deduct_points += human - model
        per_q.append({"q": qid, "human": human, "model": model, "max": truth["max"],
                      "status": status, "disputed": disputed,
                      "abs_err": (abs(model - human) if (model is not None and _is_open(qid) and not disputed) else None)})
    return {
        "open_mae": statistics.mean(open_abs) if open_abs else None,
        "open_max_err": max(open_abs) if open_abs else None,
        "tf_mc_exact": (tf_mc_hits / tf_mc_total) if tf_mc_total else None,
        "tf_mc_hits": tf_mc_hits, "tf_mc_total": tf_mc_total,
        "false_zeros": false_zeros, "escalations": escalations,
        "false_deductions": false_deducts, "full_credit_items": full_credit_items,
        "false_deduction_points": false_deduct_points,
        "escalation_precision": (esc_tp / (esc_tp + esc_fp)) if (esc_tp + esc_fp) else None,
        "escalation_recall": (esc_tp / (esc_tp + esc_fn)) if (esc_tp + esc_fn) else None,
        "esc_tp": esc_tp, "esc_fp": esc_fp, "esc_fn": esc_fn,
        "per_question": per_q,
    }


def run(live: bool) -> dict:
    gts = load_ground_truth()
    booklets = discover_booklets()
    from .config import CONFIG
    report: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": live,
                    "config": CONFIG.to_log(),  # a number is only a measurement with its config
                    "n_ground_truth": len(gts), "n_booklets": len(booklets),
                    "routing": routing_accuracy(gts),
                    "ink": ink_pass(booklets),  # FREE, whole corpus
                    "grading": []}
    if live:
        for gt in gts:
            got, cost = grade_booklet_live(gt)
            report["grading"].append({"exam_id": gt["exam_id"], "cost": cost, **score_grading(gt, got)})
    return report


def _print_scorecard(report: dict) -> None:
    r = report["routing"]
    print("=" * 72)
    print(f"CheckMate eval  ·  ground-truth {report['n_ground_truth']}  ·  corpus {report['n_booklets']}"
          f"  ·  {'LIVE' if report['live'] else 'offline (free deterministic pass)'}")
    print("=" * 72)
    acc = r["accuracy"]
    print(("Routing accuracy : %s (%d)" % (f"{acc:.0%}" if acc is not None else "n/a", r["n"])))
    for row in r["rows"]:
        print(f"  {'ok ' if row['ok'] else 'XX '} {row['exam_id']:16} pred={row['predicted']}  true={row['true']}")

    ink = report.get("ink", {})
    print(f"\nInk pass (FREE, whole corpus) : {ink.get('n', 0)} booklet(s)")
    print(f"  {'booklet':40} {'crs':6} {'pg':>3} {'inked_cells':>11} {'red_pg':>6} {'stu_frac':>8}")
    for row in ink.get("rows", []):
        print(f"  {row['booklet'][:40]:40} {row['course']:6} {row['pages']:>3} "
              f"{row['inked_cells']:>11} {row['red_pages']:>6} {row['avg_student_frac']:>8}")
    print("  ('region contains ink' side of the false-zero metric; the inked-vs-empty-"
          "transcript JOIN needs a cached OCR pass.)")

    if not report["live"]:
        print("\nGrading metrics : not run (pass --live; costs grader budget).")
        return
    for g in report["grading"]:
        print(f"\n[{g['exam_id']}]")
        mae = g["open_mae"]
        print(f"  open MAE       : {mae:.2f}" + (f"  (max err {g['open_max_err']:.0f})" if mae is not None else "") if mae is not None else "  open MAE       : n/a")
        tfm = g["tf_mc_exact"]
        print(f"  T/F+MC exact   : {tfm:.0%} ({g['tf_mc_hits']}/{g['tf_mc_total']})" if tfm is not None else "  T/F+MC exact   : n/a")
        print(f"  false zeros    : {g['false_zeros']}")
        print(f"  false deducts  : {g['false_deductions']}/{g['full_credit_items']} items, "
              f"{g['false_deduction_points']} pts wrongly removed from flawless work")
        ep, er = g["escalation_precision"], g["escalation_recall"]
        print(f"  escalation P/R : P={ep:.0%} R={er:.0%}  (tp={g['esc_tp']} fp={g['esc_fp']} fn={g['esc_fn']})"
              if ep is not None and er is not None
              else f"  escalation P/R : P={'n/a' if ep is None else f'{ep:.0%}'} R={'n/a' if er is None else f'{er:.0%}'}  (tp={g['esc_tp']} fp={g['esc_fp']} fn={g['esc_fn']})")
        print(f"  {'question':7} {'human':>7} {'model':>7} {'Δ':>4}  status")
        for q in g["per_question"]:
            d = f"{q['abs_err']:.0f}" if q["abs_err"] is not None else "-"
            mdl = q["model"] if q["model"] is not None else "-"
            tag = "  DISPUTED (excl.)" if q.get("disputed") else ""
            print(f"    {q['q']:7} {str(q['human'])+'/'+str(q['max']):>7} {str(mdl):>7} {d:>4}  {q['status']}{tag}")
        c = g.get("cost", {})
        stagec = " ".join(f"{k}=${c[k]:.4f}" for k in ("Parser", "Grader", "Reflector") if k in c)
        print(f"  cost (est)     : total ${c.get('total', 0):.4f}   {stagec}"
              + ("   [parse cached]" if c.get("parse_cached") else ""))


def main() -> None:
    live = "--live" in sys.argv
    report = run(live)
    os.makedirs(_REPORTS, exist_ok=True)
    path = os.path.join(_REPORTS, f"report_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    _print_scorecard(report)
    print(f"\nreport -> {os.path.relpath(path, _ROOT)}")


if __name__ == "__main__":
    main()
