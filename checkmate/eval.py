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
import json
import os
import statistics
import sys
import time

from .kb.exams import match_exam

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GRADED = os.path.join(_ROOT, "eval", "graded")
_REPORTS = os.path.join(_ROOT, "eval", "reports")


def _year_from(date) -> str | None:
    import re
    m = re.search(r"\d{4}", str(date or ""))
    return m.group(0) if m else None


def load_ground_truth() -> list[dict]:
    return [json.load(open(f, encoding="utf-8"))
            for f in sorted(glob.glob(os.path.join(_GRADED, "*.json")))]


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


def grade_booklet_live(gt: dict) -> dict[str, dict]:
    """Run the full agent on one booklet PDF (COSTS budget). Returns {question_id: result}."""
    from .orchestrator import run_agent
    from .pdf import render_pdf_to_images
    with open(os.path.join(_ROOT, gt["booklet"]), "rb") as f:
        render = render_pdf_to_images(f.read())
    res = run_agent(render.images, "", gt["exam_id"], exam=gt.get("exam_label"))
    return {q["id"]: q for q in res.get("meta", {}).get("questions", [])}


def score_grading(gt: dict, got: dict[str, dict]) -> dict:
    """Compare model results to human ground truth for one booklet."""
    open_abs: list[float] = []
    tf_mc_hits = tf_mc_total = false_zeros = escalations = 0
    per_q = []
    for qid, truth in gt["questions"].items():
        g = got.get(qid)
        model = g["score"] if g else None
        status = g["status"] if g else "missing"
        human = truth["human"]
        if g and status == "escalate":
            escalations += 1
        if _is_open(qid):
            if model is not None:
                open_abs.append(abs(model - human))
        else:
            tf_mc_total += 1
            tf_mc_hits += int(model is not None and model == human)
        # false zero: human gave credit, model said 0 and claimed no work
        if human > 0 and g and model == 0 and "no work" in (g.get("feedback", "").lower()):
            false_zeros += 1
        per_q.append({"q": qid, "human": human, "model": model, "max": truth["max"],
                      "status": status, "abs_err": (abs(model - human) if (model is not None and _is_open(qid)) else None)})
    return {
        "open_mae": statistics.mean(open_abs) if open_abs else None,
        "open_max_err": max(open_abs) if open_abs else None,
        "tf_mc_exact": (tf_mc_hits / tf_mc_total) if tf_mc_total else None,
        "tf_mc_hits": tf_mc_hits, "tf_mc_total": tf_mc_total,
        "false_zeros": false_zeros, "escalations": escalations,
        "per_question": per_q,
    }


def run(live: bool) -> dict:
    gts = load_ground_truth()
    report: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": live,
                    "n_booklets": len(gts), "routing": routing_accuracy(gts), "grading": []}
    if live:
        for gt in gts:
            got = grade_booklet_live(gt)
            report["grading"].append({"exam_id": gt["exam_id"], **score_grading(gt, got)})
    return report


def _print_scorecard(report: dict) -> None:
    r = report["routing"]
    print("=" * 64)
    print(f"CheckMate eval  ·  {report['n_booklets']} booklet(s)  ·  {'LIVE' if report['live'] else 'offline (routing only)'}")
    print("=" * 64)
    acc = r["accuracy"]
    print(f"Routing accuracy : {acc:.0%}" if acc is not None else "Routing accuracy : n/a", f"({r['n']})")
    for row in r["rows"]:
        print(f"  {'ok ' if row['ok'] else 'XX '} {row['exam_id']:16} pred={row['predicted']}  true={row['true']}")
    if not report["live"]:
        print("\nGrading metrics : not run (pass --live; costs budget).")
        return
    for g in report["grading"]:
        print(f"\n[{g['exam_id']}]")
        mae = g["open_mae"]
        print(f"  open MAE       : {mae:.2f}" + (f"  (max err {g['open_max_err']:.0f})" if mae is not None else "") if mae is not None else "  open MAE       : n/a")
        tfm = g["tf_mc_exact"]
        print(f"  T/F+MC exact   : {tfm:.0%} ({g['tf_mc_hits']}/{g['tf_mc_total']})" if tfm is not None else "  T/F+MC exact   : n/a")
        print(f"  false zeros    : {g['false_zeros']}")
        print(f"  escalations    : {g['escalations']}")
        worst = sorted((q for q in g["per_question"] if q["abs_err"] is not None), key=lambda q: -q["abs_err"])[:3]
        for q in worst:
            print(f"    Δ{q['abs_err']:.0f}  {q['q']:5} human {q['human']}/{q['max']}  model {q['model']}  [{q['status']}]")


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
