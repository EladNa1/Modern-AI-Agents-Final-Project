"""Capture the 'golden examples' we state to the lecturer: run each stated prompt against a
running CheckMate server and save the full request -> response -> steps transcript.

Run (server must be up):
    python scripts/capture_examples.py [--base http://127.0.0.1:8000]

Writes eval/examples/<name>.json plus a summary index eval/examples/README.md.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1].rstrip("/")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "eval", "examples")

# The stated examples. Each is a JSON /api/execute call the grader can replay verbatim.
EXAMPLES = [
    {
        "name": "01_grade_sample_booklet",
        "title": "Grade a real scanned booklet (full agent, cached vision)",
        "prompt": "Grade sample booklet 1",
        "expect": "Real Router→Retriever→Grader→Reflector run; per-question grades + trace.",
    },
    {
        "name": "02_offtopic_refusal",
        "title": "Out-of-domain request — polite refusal, zero tokens",
        "prompt": "How do I bake sourdough bread?",
        "expect": "status ok, Router REFUSE step, no model calls.",
    },
    {
        "name": "03_grading_needs_input",
        "title": "In-scope request with nothing to grade — agent asks for input",
        "prompt": "Please grade my calculus exam",
        "expect": "status ok, Router NEED_INPUT step listing the sample booklets.",
    },
    {
        "name": "04_grade_sample_booklet_2",
        "title": "Grade the second bundled booklet (same exam, different student)",
        "prompt": "Grade sample booklet 2",
        "expect": "Real run on the second student's work; human cover total is 93/100.",
    },
]


def post(prompt: str) -> dict:
    req = urllib.request.Request(
        BASE + "/api/execute",
        data=json.dumps({"prompt": prompt}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=290) as r:
        return json.load(r)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    index = ["# CheckMate — stated examples (captured transcripts)", "",
             f"Captured against `{BASE}` on {time.strftime('%Y-%m-%d %H:%M')}.", ""]
    for ex in EXAMPLES:
        t0 = time.time()
        resp = post(ex["prompt"])
        dt = time.time() - t0
        record = {"title": ex["title"], "request": {"prompt": ex["prompt"]},
                  "elapsed_seconds": round(dt, 1), "response": resp}
        path = os.path.join(OUT, ex["name"] + ".json")
        json.dump(record, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        n_steps = len(resp.get("steps") or [])
        cost = (resp.get("meta") or {}).get("cost", {}).get("total")
        print(f"{ex['name']}: status={resp.get('status')} steps={n_steps} "
              f"time={dt:.0f}s cost={cost}")
        index += [f"## {ex['title']}", "",
                  f'- prompt: `{ex["prompt"]}`',
                  f"- expected: {ex['expect']}",
                  f"- observed: status `{resp.get('status')}`, {n_steps} steps, {dt:.0f}s"
                  + (f", est. cost ${cost}" if cost else ""),
                  f"- transcript: `{ex['name']}.json`", ""]
    open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("\n".join(index))
    print("wrote", os.path.join(OUT, "README.md"))


if __name__ == "__main__":
    main()
