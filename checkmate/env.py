"""Configuration for the LLMod.ai gateway (OpenAI-compatible) and Pinecone.

Values come from environment variables: .env.local in dev, Vercel env in prod. The
.env.local loader uses setdefault, so real platform env always wins on Vercel.
Port of lib/env.ts.
"""
from __future__ import annotations

import os


def _load_env_local() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env.local")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env_local()

LLMOD_BASE_URL = os.environ.get("LLMOD_BASE_URL", "https://api.llmod.ai/v1")
LLMOD_KEY = os.environ.get("LLMOD_KEY", "")
LLMOD_MODEL = os.environ.get("LLMOD_MODEL", "MB5R2CF-azure/gpt-5.4-mini")
LLMOD_EMBED_MODEL = os.environ.get("LLMOD_EMBED_MODEL", "MB5R2CF-azure/text-embedding-3-small")

# True when a key is configured -- the real agent runs; otherwise callers fall back to the
# mock (checkmate/mock_agent.py) so the app still works with no secrets.
HAS_LLM = len(LLMOD_KEY) > 0

# Pinecone (vector RAG). Optional: when unset, the Retriever falls back to the bundled-JSON
# exact-match, so the agent keeps working with no vector DB.
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX", "checkmate-kb")
EMBED_DIM = 1536  # text-embedding-3-small
HAS_PINECONE = len(PINECONE_API_KEY) > 0
