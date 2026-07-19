// Pinecone vector store for the Knowledge base (Phase 7).
// The Retriever queries it semantically; scripts/index_kb.ts populates it.
// Metadata carries the whole solution entry so a match needs no second lookup.
import { Pinecone, type Index } from "@pinecone-database/pinecone";
import { PINECONE_API_KEY, PINECONE_INDEX, EMBED_DIM } from "../env";

// Two record kinds share one index (filter with { kind: { $eq: ... } }):
//   "solution" — one official exam-question solution (grounds the grade + point value)
//   "notes"    — a chunk of course lecture material (extra grounding context)
export type KBKind = "solution" | "notes";

export type SolutionMetadata = {
  kind: "solution";
  entryId: string; // e.g. "Q3c"
  points: number;
  topic: string;
  problem: string;
  official_solution: string;
  final_answer: string;
  notes: string;
  exam: string; // e.g. "2023w final A"
  course: string; // e.g. "104041"
};

export type NotesMetadata = {
  kind: "notes";
  source: string; // e.g. "Hedva 1 lecture notes"
  page: number;
  text: string; // the chunk text
};

export type KBMetadata = SolutionMetadata | NotesMetadata;

let _pc: Pinecone | null = null;
function pc(): Pinecone {
  if (!_pc) _pc = new Pinecone({ apiKey: PINECONE_API_KEY });
  return _pc;
}

export function kbIndex(): Index<KBMetadata> {
  return pc().index<KBMetadata>(PINECONE_INDEX);
}

// Create the serverless index if absent (idempotent) — called by the indexer so
// a fresh Pinecone account needs no console clicks. dim/metric match the embed model.
export async function ensureIndex(): Promise<void> {
  const list = await pc().listIndexes();
  if (list.indexes?.some((i) => i.name === PINECONE_INDEX)) return;
  await pc().createIndex({
    name: PINECONE_INDEX,
    dimension: EMBED_DIM,
    metric: "cosine",
    spec: { serverless: { cloud: "aws", region: "us-east-1" } },
    waitUntilReady: true,
  });
}
