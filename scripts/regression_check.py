"""Regression check: the bundled sample-booklet captures vs the human ground truth.

Zero LLM calls -- everything runs over the committed JSON. Run after any change that
regrades a bundled booklet (and before pushing refreshed captures):

    python scripts/regression_check.py

Asserts, per booklet:
  - spec shape: {status, error, response, steps}, each step {module, prompt, response}
  - no output artifacts (ANSI remnants, orphaned LaTeX escapes, control chars)
  - module names match the architecture (and 'Orchestrator' is never a step module)
  - ta_review == the escalated questions (no contradiction with the summary line)
  - open questions: |agent - human| <= TOLERANCE points, or the question is escalated
  - T/F + MC: exact match with the human mark, or escalated
  - the unmatched-writing bucket (if present) is 0/0, escalated, zero-LLM
Exit code 0 = all green; 1 = regression.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "checkmate", "kb", "samples")
TRUTH = os.path.join(ROOT, "eval", "graded", "104041-2024W-A.json")

TOLERANCE = 3          # open-question points the agent may differ from the human unscathed
                       # (inter-grader variance on a 15-pt proof question is easily this much)
EXPECTED_MODULES = {"Config", "Parser", "Router", "Retriever", "Grader", "Reflector",
                    "GradeBook", "Zoom"}

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("ok  " if cond else "XX  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def is_tf_mc(qid: str) -> bool:
    u = qid.upper()
    return u.startswith("MC") or u.startswith("TF")


truth = json.load(open(TRUTH, encoding="utf-8"))["questions"]

BOOKLETS = [
    # (capture file, human ground truth applies?, label)
    ("example_run_b1.json", True, "booklet 1 (human 90)"),
    ("example_run.json", False, "booklet 2 (human 93; per-question sheet not transcribed)"),
]

for fname, has_truth, label in BOOKLETS:
    rec = json.load(open(os.path.join(SAMPLES, fname), encoding="utf-8"))
    resp = rec["response"]
    meta = resp["meta"]
    raw = json.dumps(resp, ensure_ascii=False)
    print(f"--- {label}: total {meta['total']}/{meta['max']} ---")

    check(f"{fname}: spec 4-key shape",
          sorted(k for k in resp if k != "meta") == ["error", "response", "status", "steps"])
    check(f"{fname}: step shape",
          all(sorted(k for k in s if k != "pattern") == ["module", "prompt", "response"]
              and sorted(s["prompt"]) == ["System_prompt", "User_prompt"] for s in resp["steps"]))
    mods = {s["module"] for s in resp["steps"]}
    check(f"{fname}: modules within architecture", mods <= EXPECTED_MODULES, str(mods))
    check(f"{fname}: no ANSI/ctrl artifacts",
          not re.search(r"\[[0-9]{1,2};[0-9]{1,2}m|\[0m|[\x00-\x08\x0b\x0c\x0e-\x1f]", raw))
    check(f"{fname}: no orphaned LaTeX escapes", "rac{" not in raw.replace("frac{", ""))

    esc = sorted(q["id"] for q in meta["questions"] if q["status"] == "escalate")
    ta = sorted(r["q"] for r in (meta.get("gradebook") or {}).get("ta_review", []))
    check(f"{fname}: ta_review == escalations", ta == esc, f"ta={ta} esc={esc}")
    if esc:
        check(f"{fname}: summary discloses escalation", "escalated to the teacher" in resp["response"])
    else:
        check(f"{fname}: summary says none", "No questions required human review" in resp["response"])

    for q in meta["questions"]:
        qid = q["id"]
        if qid == "Unmatched work":
            check(f"{fname}: unmatched bucket honest",
                  q["score"] == 0 and q["max"] == 0 and q["status"] == "escalate")
            continue
        if not has_truth or qid not in truth:
            continue
        human = truth[qid]["human"]
        if q["status"] == "escalate":
            continue  # escalated points are the human's decision, not a delta to measure
        if is_tf_mc(qid):
            check(f"{fname}: {qid} exact vs human", q["score"] == human,
                  f"agent {q['score']} vs human {human}")
        else:
            check(f"{fname}: {qid} within {TOLERANCE} of human", abs(q["score"] - human) <= TOLERANCE,
                  f"agent {q['score']} vs human {human}")

print()
print("REGRESSION:", "ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
