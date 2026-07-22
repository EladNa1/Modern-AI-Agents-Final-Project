"""Dev harness -- embed every bundled KB solution and upsert to Pinecone.
Port of scripts/index_kb.ts. Auto-creates the index if missing. Re-runnable.

Run: python scripts/index_kb.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkmate.env import HAS_PINECONE, PINECONE_INDEX  # noqa: E402
from checkmate.kb.pinecone import ensure_index, kb_index  # noqa: E402
from checkmate.llm import embed  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KB = os.path.join(_ROOT, "checkmate", "kb")
_SOL_DIR = os.path.join(_KB, "solutions")


def embed_text(e: dict) -> str:
    """What we embed: the QUESTION statement (topic + problem), so a student's transcribed
    work matches the problem it answers -- not the solution text."""
    return "\n".join(x for x in [e.get("topic"), e.get("problem")] if x)[:4000]


def main() -> None:
    if not HAS_PINECONE:
        print("PINECONE_API_KEY not set — nothing to index.")
        sys.exit(1)

    files = sorted(f for f in os.listdir(_SOL_DIR) if f.endswith(".json"))
    print(f"KB files: {', '.join(files) or '(none)'}")

    # Stamp this run. Every vector written now carries it; the Retriever only trusts vectors
    # from the generation recorded in generation.json.
    gen = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ids: list[str] = []
    texts: list[str] = []
    metas: list[dict] = []

    for f in files:
        kb = json.load(open(os.path.join(_SOL_DIR, f), encoding="utf-8"))
        slug = f[:-5]
        for e in kb["questions"].values():
            ids.append(f"sol:{slug}:{e['id']}")
            texts.append(embed_text(e))
            metas.append({
                "kind": "solution", "gen": gen, "entryId": e["id"], "points": e.get("points", 0),
                "topic": e.get("topic", "") or "", "problem": e.get("problem", ""),
                "official_solution": e.get("official_solution", ""),
                "final_answer": e.get("final_answer", "") or "", "notes": e.get("notes", "") or "",
                "exam": kb["exam"], "course": kb["course"],
            })

    if not ids:
        print("No KB entries found.")
        sys.exit(1)

    print(f"Embedding {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}…")
    vectors = embed(texts)

    print(f'Ensuring index "{PINECONE_INDEX}"…')
    ensure_index()

    index = kb_index()
    records = [{"id": ids[i], "values": vectors[i], "metadata": metas[i]} for i in range(len(ids))]
    index.upsert(vectors=records)
    print(f"Upserted {len(records)} vectors:")
    for r in records:
        print(f"  - {r['id']}  ({r['metadata']['exam']} · {r['metadata']['points']} pts)")

    # Record the generation the app should trust. Upsert overwrites by id but never removes,
    # so writing the stamp here is what retires vectors an earlier run left behind.
    keep = set(ids)
    superseded = 0
    token = None
    while True:
        page = index.list_paginated(prefix="sol:", pagination_token=token)
        page_vectors = getattr(page, "vectors", None) or (page.get("vectors") if isinstance(page, dict) else [])
        for v in page_vectors or []:
            vid = getattr(v, "id", None) or (v.get("id") if isinstance(v, dict) else None)
            if vid and vid not in keep:
                superseded += 1
        pagination = getattr(page, "pagination", None) or (page.get("pagination") if isinstance(page, dict) else None)
        token = getattr(pagination, "next", None) or (pagination.get("next") if isinstance(pagination, dict) else None)
        if not token:
            break

    with open(os.path.join(_KB, "generation.json"), "w", encoding="utf-8") as f:
        json.dump({"gen": gen, "indexed": len(ids)}, f, ensure_ascii=False, indent=2)
    print(f"Generation {gen} — {len(ids)} live vector(s).")
    if superseded:
        print(f"{superseded} superseded vector(s) remain in the index but are now filtered out of retrieval.")

    time.sleep(3)  # give the serverless index a moment
    stats = index.describe_index_stats()
    total = getattr(stats, "total_vector_count", None)
    if total is None and isinstance(stats, dict):
        total = stats.get("total_vector_count")
    print("index total vectors:", total)


if __name__ == "__main__":
    main()
