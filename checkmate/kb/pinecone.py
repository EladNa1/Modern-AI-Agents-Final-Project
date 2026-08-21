"""Pinecone vector store for the knowledge base. The Retriever queries it semantically;
scripts/index_kb.py populates it. Metadata carries the whole solution entry so a match
needs no second lookup. Port of lib/kb/pinecone.ts.

Two record kinds share one index (filter with {"kind": {"$eq": ...}}):
  "solution" -- one official exam-question solution (grounds the grade + point value)
  "notes"    -- a chunk of course lecture material (extra grounding context)
"""
from __future__ import annotations

import os

from pinecone import Pinecone, ServerlessSpec

from ..env import EMBED_DIM, PINECONE_API_KEY, PINECONE_INDEX

# Retrieval runs BEFORE the grading loop (one query per transcribed fragment), so it sits
# outside the run's wall-clock guard -- and the SDK ships with no request timeout, so a slow
# Pinecone hangs on an SSL read until Vercel kills the whole request at 300s. The Retriever
# already treats an exception as "Pinecone unavailable" and degrades to the bundled-JSON
# fallback, so a bounded failure is strictly better than an unbounded wait.
PINECONE_TIMEOUT_SECONDS = int(os.environ.get("CHECKMATE_PINECONE_TIMEOUT") or 10)

_pc: Pinecone | None = None


def _client() -> Pinecone:
    global _pc
    if _pc is None:
        _pc = Pinecone(api_key=PINECONE_API_KEY, timeout=PINECONE_TIMEOUT_SECONDS)
    return _pc


def kb_index():
    return _client().Index(PINECONE_INDEX)


def ensure_index() -> None:
    """Create the serverless index if absent (idempotent) -- so a fresh Pinecone account
    needs no console clicks. dim/metric match the embed model."""
    pc = _client()
    try:
        if pc.has_index(PINECONE_INDEX):
            return
    except AttributeError:
        # Older client without has_index: fall back to listing names.
        if PINECONE_INDEX in set(pc.list_indexes().names()):
            return
    pc.create_index(
        name=PINECONE_INDEX,
        dimension=EMBED_DIM,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
