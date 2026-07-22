"""Pinecone vector store for the knowledge base. The Retriever queries it semantically;
scripts/index_kb.py populates it. Metadata carries the whole solution entry so a match
needs no second lookup. Port of lib/kb/pinecone.ts.

Two record kinds share one index (filter with {"kind": {"$eq": ...}}):
  "solution" -- one official exam-question solution (grounds the grade + point value)
  "notes"    -- a chunk of course lecture material (extra grounding context)
"""
from __future__ import annotations

from pinecone import Pinecone, ServerlessSpec

from ..env import EMBED_DIM, PINECONE_API_KEY, PINECONE_INDEX

_pc: Pinecone | None = None


def _client() -> Pinecone:
    global _pc
    if _pc is None:
        _pc = Pinecone(api_key=PINECONE_API_KEY)
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
