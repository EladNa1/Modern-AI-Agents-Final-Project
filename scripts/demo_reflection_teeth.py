"""Demonstrate the Reflector's teeth on SEEDED-WRONG grades (audit round 12).

A healthy run gives the Reflector nothing to fix -- every bundled trace showing
APPROVE is the loop agreeing with a Grader that was right. This script proves the
loop is not a rubber stamp: it hands the live Reflector two deliberately wrong
proposed grades and records what it does.

  Seed A: booklet-1 Q2b (real derivative sign slip, anchored rubric 7/10)
          proposed WRONG grade: 10/10 "flawless".
  Seed B: booklet-1 Q1a (complete correct monotonicity proof, human 10/10)
          proposed WRONG grade: 3/10 "mostly wrong".

Expected: neither is APPROVEd -- the Reflector must REVISE toward the anchored /
correct score or ESCALATE. Output is saved as eval/examples/05_reflection_teeth.json
(two real LLM calls, ~$0.01).

Run:  python scripts/demo_reflection_teeth.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.llm import StepLog  # noqa: E402
from checkmate.models import Grade, ParsedFragment  # noqa: E402
from checkmate.reflector import run_reflector  # noqa: E402
from checkmate.retriever import _find_exact, _norm_id  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAM = "2024w moed A"


def merged_fragment(display_id: str, picks: list[tuple[str, int]]) -> ParsedFragment:
    """Concatenate the booklet-1 fragments at the given (parser label, page) pins.

    Pinned by (id, page) rather than by label alone because parser labels LIE -- in this
    very booklet the fragment labeled 'Q2b' is Q1b's integral work, and the real Q2b work
    sits under a bare 'ב' on page 9 (in the live pipeline the Retriever fixes this by
    matching CONTENT; this demo reproduces the fixed grouping directly)."""
    d = json.load(open(os.path.join(ROOT, "checkmate", "kb", "samples", "104041-2024W-A.json"),
                       encoding="utf-8"))
    by_pin = {(f.get("id"), f.get("page")): f for f in d["fragments"]}
    parts = []
    for pin in picks:
        if pin not in by_pin:
            raise SystemExit(f"fragment pin {pin} not found in the bundled parse")
        parts.append(by_pin[pin]["text"])
    conf = min(by_pin[pin].get("confidence", 0.9) for pin in picks)
    return ParsedFragment(id=display_id, text="\n\n".join(parts), latex=None,
                          confidence=conf, page=picks[0][1])


SEEDS = [
    {
        "name": "Q2b seeded 10/10 (real sign slip on the page; anchored rubric says exactly 7/10)",
        "qid": "q2b", "pins": [("ב", 9)], "expected": "REVISE toward 7 or ESCALATE",
        "grade": dict(score=10, max=10, status="ok",
                      feedback="Flawless solution. The derivative and monotonicity argument are perfect.",
                      justification="No errors found anywhere.", confidence=0.95),
    },
    {
        "name": "Q1a seeded 3/10 (complete correct proof; human gave 10/10)",
        "qid": "q1a", "pins": [("Q1א", 5), ("Q3c", 6)], "expected": "REVISE toward 10 or ESCALATE",
        "grade": dict(score=3, max=10, status="partial",
                      feedback="The monotonicity argument is mostly wrong and the derivative bound is unjustified.",
                      justification="Major gaps throughout.", confidence=0.9),
    },
]

log = StepLog()
cases = []
teeth = 0
for seed in SEEDS:
    retrieved = _find_exact(_norm_id(seed["qid"]), EXAM)
    if retrieved is None:
        raise SystemExit(f"KB entry {seed['qid']} not found")
    frag = merged_fragment(retrieved.entry.id, seed["pins"])
    wrong = Grade(id=retrieved.entry.id, **seed["grade"])
    refl = run_reflector(frag, wrong, retrieved, log)
    bit = refl.action in ("REVISE", "ESCALATE")
    teeth += bit
    print(f"{seed['name']}\n  -> {refl.action}"
          + (f" to {refl.score}" if refl.score is not None else "")
          + f" | note: {refl.note[:100]}")
    cases.append({"seed": seed["name"], "expected": seed["expected"],
                  "seeded_grade": {"score": wrong.score, "max": wrong.max,
                                   "feedback": wrong.feedback},
                  "reflection": {"action": refl.action, "score": refl.score,
                                 "feedback": refl.feedback, "note": refl.note,
                                 "confidence": refl.confidence}})

out = {
    "title": "Reflection has teeth: the Reflector on two seeded-WRONG grades",
    "what_this_shows": (
        "Every APPROVE in the bundled runs is the loop agreeing with a Grader that was "
        "right. Here the proposed grades are deliberately wrong -- a 10/10 for work with "
        "a real anchored error, and a 3/10 for a complete correct proof -- and the live "
        "Reflector rejects both. Reproduce with: python scripts/demo_reflection_teeth.py"),
    "cases": cases,
    "steps": [s.__dict__ for s in log.steps],
}
dest = os.path.join(ROOT, "eval", "examples", "05_reflection_teeth.json")
json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\nsaved {dest}")
print("TEETH:", "DEMONSTRATED" if teeth == len(SEEDS) else f"ONLY {teeth}/{len(SEEDS)} — prompt needs rebalancing")
sys.exit(0 if teeth == len(SEEDS) else 1)
