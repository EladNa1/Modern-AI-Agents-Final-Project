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

    # --- Trace question-id integrity (audit f4b64c4) -------------------------------
    # A Grader/Reflector step must name the question it is actually grading. The prompt
    # header used to carry ParsedFragment.id -- the label the vision Parser read off the
    # handwriting -- while the grounding block beneath it carried the KB id that retrieval
    # had matched. So a step could be headed "Question Q2b" while quoting Q1b's official
    # solution, and an MC item could be headed "Q3" while a different, real Q3 existed on
    # the same exam. Two assertions, both falsifiable against a real capture:
    #   1. every header id is a question this run actually graded
    #   2. the header id equals the KB id of the solution quoted in the SAME prompt
    graded_ids = {q["id"] for q in meta["questions"]}
    hdr_re = {
        "Grader": re.compile(r"\AGrade question (.+?)\. Maximum score:"),
        "Reflector": re.compile(r"\AQuestion (.+?) \(max \d+ points\)\."),
    }
    ground_re = re.compile(r"(?m)^Question (.+?) — worth [0-9]+ points\.$")
    unknown: list[str] = []
    mismatched: list[str] = []
    for s in resp["steps"]:
        pat = hdr_re.get(s["module"])
        if pat is None:
            continue
        up = s["prompt"]["User_prompt"]
        m = pat.match(up)
        if not m:
            continue
        header = m.group(1)
        if header not in graded_ids:
            unknown.append(f'{s["module"]}:{header}')
        g = ground_re.search(up)
        if g and g.group(1) != header:
            mismatched.append(f'{s["module"]}:{header}!={g.group(1)}')
    check(f"{fname}: trace ids name a graded question", not unknown,
          f"{len(unknown)} bad, e.g. {sorted(set(unknown))[:6]}")
    check(f"{fname}: trace id matches the solution quoted in the same prompt", not mismatched,
          f"{len(mismatched)} bad, e.g. {sorted(set(mismatched))[:6]}")

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

# --- Retrieval negative control (audit round 11): content from ANOTHER exam, under this
# exam's scope, with an id the KB does not hold, must produce NO match (-> the escalated
# unmatched bucket), never a look-alike grounding. Offline: Pinecone is forced off so the
# check exercises the content-overlap + exact-id fallbacks deterministically.
sys.path.insert(0, ROOT)
import checkmate.retriever as _retr  # noqa: E402
from checkmate.llm import StepLog as _StepLog  # noqa: E402

_had = _retr.HAS_PINECONE
_retr.HAS_PINECONE = False
try:
    foreign = _retr.retrieve(
        "Q9x", "Find the eigenvalues of the matrix A and prove the rank-nullity theorem "
               "for the linear transformation T", _StepLog(), exam="2024w moed A")
    check("negative control: foreign-exam content finds no match", foreign is None,
          str(foreign and foreign.entry.id))
finally:
    _retr.HAS_PINECONE = _had

print()
print("REGRESSION:", "ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
