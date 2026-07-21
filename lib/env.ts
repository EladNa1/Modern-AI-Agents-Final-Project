// Server-only configuration for the LLMod.ai gateway (OpenAI-compatible).
// Values come from environment variables: .env.local in dev, Vercel env in prod.
// Do NOT import this into client components — it reads secrets.

export const LLMOD_BASE_URL =
  process.env.LLMOD_BASE_URL ?? "https://api.llmod.ai/v1";
export const LLMOD_KEY = process.env.LLMOD_KEY ?? "";
export const LLMOD_MODEL =
  process.env.LLMOD_MODEL ?? "MB5R2CF-azure/gpt-5.4-mini";
export const LLMOD_EMBED_MODEL =
  process.env.LLMOD_EMBED_MODEL ?? "MB5R2CF-azure/text-embedding-3-small";

// True when a key is configured — the real agent runs; otherwise callers fall
// back to the mock (lib/mockAgent.ts) so the app still works with no secrets.
export const hasLLM = LLMOD_KEY.length > 0;

// Pinecone (vector RAG). Optional: when unset, the Retriever falls back to the
// bundled-JSON exact-match, so the agent keeps working with no vector DB.
export const PINECONE_API_KEY = process.env.PINECONE_API_KEY ?? "";
export const PINECONE_INDEX = process.env.PINECONE_INDEX ?? "checkmate-kb";
export const EMBED_DIM = 1536; // text-embedding-3-small
export const hasPinecone = PINECONE_API_KEY.length > 0;
