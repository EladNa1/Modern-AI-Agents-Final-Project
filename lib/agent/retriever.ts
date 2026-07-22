// Retriever module (deck slide 7) — the RAG stage.
// Grounds each question in the Knowledge base: returns the official solution,
// point value, and notes for the matched exam question. Two layers:
//   1. exact question-id match over app-bundled JSON (fast, deterministic, offline)
//   2. Pinecone vector search over the embedded KB (semantic — catches misread ids
//      and questions the parser numbered wrong). Falls back gracefully when Pinecone
//      is not configured or is unreachable, so grading never breaks.
import { StepLog, embed } from "../llm";
import { hasPinecone } from "../env";
import { kbIndex, type SolutionMetadata, type NotesMetadata } from "../kb/pinecone";
import generation from "../kb/generation.json";
import { EXAMS, type ExamKB, type SolutionEntry } from "../kb/exams";

export type { SolutionEntry };

// Registry of bundled exams (offline exact-id fallback), from the shared source of truth
// so a newly ingested exam is picked up here too. Pinecone holds the same content
// embedded — the PRIMARY, content-based matcher — which, once scoped to the right exam,
// disambiguates an id shared across exams (e.g. "Q1" exists in every exam).
const KB: ExamKB[] = EXAMS;

export type Retrieved = {
  entry: SolutionEntry;
  exam: string;
  course: string;
} | null;

// Minimum cosine similarity to trust a semantic match — below this we'd rather
// ground on nothing (and let the Grader escalate) than on the wrong problem.
const MIN_SCORE = 0.5;
// Notes chunks come from directly-extracted (RTL-reordered) lecture text, so their
// cosine scores run lower than curated solutions — a gentler bar for supporting context.
const NOTES_MIN_SCORE = 0.3;

// Normalize a question label to a comparable key: "Q3(c)" / "3ג" / "Q3c" -> "3c".
const HE_PARTS: Record<string, string> = {
  "א": "a",
  "ב": "b",
  "ג": "c",
  "ד": "d",
  "ה": "e",
};
function normId(s: string): string {
  let out = (s ?? "").toString().toLowerCase();
  for (const [he, en] of Object.entries(HE_PARTS)) out = out.replaceAll(he, en);
  out = out.replace(/[^a-z0-9]/g, ""); // drop parens, spaces, punctuation
  return out.replace(/^q/, ""); // "q3c" and "3c" both -> "3c"
}

// Signature match for the Q3c limit — offline safety net for when the parsed id is
// unreliable AND Pinecone is unavailable.
function looksLikeSinSqrtLimit(text: string): boolean {
  const t = (text ?? "").toLowerCase();
  return /sin/.test(t) && /(√|sqrt)/.test(t) && /(x\^?9|x9|x⁹)/.test(t);
}

function findExact(want: string, exam?: string): Retrieved {
  for (const kb of KB) {
    // When the teacher named the exam, only its own questions may ground the grade —
    // a shared id must not pull a different exam's solution.
    if (exam && kb.exam !== exam) continue;
    for (const [key, entry] of Object.entries(kb.questions)) {
      if (normId(key) === want || normId(entry.id) === want) {
        return { entry, exam: kb.exam, course: kb.course };
      }
    }
  }
  return null;
}

function entryFromMetadata(m: SolutionMetadata): Retrieved {
  return {
    entry: {
      id: m.entryId,
      points: m.points,
      topic: m.topic || undefined,
      problem: m.problem,
      official_solution: m.official_solution,
      final_answer: m.final_answer || undefined,
      notes: m.notes || undefined,
    },
    exam: m.exam,
    course: m.course,
  };
}

// A chunk of course lecture material, for extra grounding context.
export type NotesChunk = { source: string; page: number; text: string; score: number };

// Pinecone semantic query over SOLUTION records only. Returns the best match above
// MIN_SCORE, or null. Never throws — a Pinecone outage must not break grading.
async function findSemantic(
  text: string,
  exam?: string
): Promise<{ found: Retrieved; score: number } | null> {
  try {
    const [vector] = await embed(text.slice(0, 4000));
    const res = await kbIndex().query({
      topK: 1,
      vector,
      includeMetadata: true,
      // Pin to the current indexing generation: superseded vectors from an earlier
      // ingest are still physically in the index (it rejects deletes) and would
      // otherwise ground a grade on a question that is no longer on the exam.
      // When the exam is known, scope to it as well — question ids and topics repeat
      // across exams, so an unscoped nearest-neighbour can match the wrong exam.
      filter: {
        kind: { $eq: "solution" },
        gen: { $eq: generation.gen },
        ...(exam ? { exam: { $eq: exam } } : {}),
      },
    });
    const top = res.matches?.[0];
    if (!top?.metadata || (top.score ?? 0) < MIN_SCORE) {
      return { found: null, score: top?.score ?? 0 };
    }
    return { found: entryFromMetadata(top.metadata as SolutionMetadata), score: top.score ?? 0 };
  } catch {
    return null; // treat as "Pinecone unavailable"
  }
}

// Retrieve the top-k most relevant course-notes chunks for extra grounding.
// Independent of solution retrieval; returns [] when Pinecone is off/unreachable.
export async function retrieveNotes(
  questionText: string,
  log: StepLog,
  k = 3
): Promise<NotesChunk[]> {
  if (!hasPinecone || !questionText.trim()) return [];
  let chunks: NotesChunk[] = [];
  try {
    const [vector] = await embed(questionText.slice(0, 4000));
    const res = await kbIndex().query({
      topK: k,
      vector,
      includeMetadata: true,
      filter: { kind: { $eq: "notes" } },
    });
    chunks = (res.matches ?? [])
      .filter((m) => (m.score ?? 0) >= NOTES_MIN_SCORE && m.metadata)
      .map((m) => {
        const meta = m.metadata as NotesMetadata;
        return { source: meta.source, page: meta.page, text: meta.text, score: m.score ?? 0 };
      });
  } catch {
    chunks = [];
  }
  log.add(
    "Retriever",
    "Retrieve relevant course lecture material (RAG) to give the Grader extra grounding beyond the official solution. Do not grade.",
    `Notes query: ${questionText.slice(0, 160)}`,
    { kind: "notes", hits: chunks.map((c) => ({ page: c.page, score: c.score })) },
    "RAG"
  );
  return chunks;
}

export async function retrieve(
  questionId: string,
  questionText: string,
  log: StepLog,
  exam?: string
): Promise<Retrieved> {
  const want = normId(questionId);
  let found: Retrieved = null;
  let method = "";
  let score: number | undefined;

  // Primary: semantic vector search over the embedded KB. Content-based, and — when the
  // exam is known — scoped to it, so a question id shared across exams (every exam has a
  // "Q1") grounds on the right exam's solution.
  if (hasPinecone && questionText.trim()) {
    const sem = await findSemantic(questionText, exam);
    if (sem?.found) {
      found = sem.found;
      method = "semantic";
      score = sem.score;
    } else if (sem) {
      score = sem.score; // queried, but below threshold — record why
    }
  }

  // Fallback (offline / Pinecone miss): exact question-id over the bundled KB.
  if (!found) {
    found = findExact(want, exam);
    if (found) method = "exact-id";
  }

  // Last resort (offline): the hard-coded signature for the demo limit.
  if (!found && looksLikeSinSqrtLimit(questionText)) {
    const entry = KB[0].questions["Q3c"];
    if (entry) {
      found = { entry, exam: KB[0].exam, course: KB[0].course };
      method = "signature";
    }
  }

  log.add(
    "Retriever",
    "Match the exam question to the knowledge base and return the official solution and point value for grounding the grade. Do not grade.",
    `Question ${questionId}: ${(questionText ?? "").slice(0, 200)}`,
    found
      ? {
          matched: found.entry.id,
          method,
          score,
          exam: found.exam,
          course: found.course,
          points: found.entry.points,
          final_answer: found.entry.final_answer,
        }
      : {
          matched: null,
          method: hasPinecone ? "searched" : "no-vector-db",
          score,
          reason: "no knowledge-base entry above the match threshold",
        },
    "RAG"
  );

  return found;
}
