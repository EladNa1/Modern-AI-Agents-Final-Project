// Dev harness (Phase 7) — embed every bundled KB solution and upsert to Pinecone.
// Auto-creates the index if missing. Re-runnable (upserts overwrite by id).
// Run: node --env-file=.env.local --import tsx scripts/index_kb.ts
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { embed } from "../lib/llm";
import { hasPinecone, PINECONE_INDEX } from "../lib/env";
import { ensureIndex, kbIndex, type SolutionMetadata } from "../lib/kb/pinecone";

type Entry = {
  id: string; points: number; topic?: string; problem: string;
  official_solution: string; final_answer?: string; notes?: string;
};
type ExamKB = { exam: string; course: string; questions: Record<string, Entry> };

// What we embed: the QUESTION statement (topic + problem), so a student's
// transcribed work matches the problem it answers — not the solution text.
function embedText(e: Entry): string {
  return [e.topic, e.problem].filter(Boolean).join("\n").slice(0, 4000);
}

async function main() {
  if (!hasPinecone) {
    console.error("PINECONE_API_KEY not set — nothing to index.");
    process.exit(1);
  }
  const dir = join(process.cwd(), "lib", "kb", "solutions");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  console.log(`KB files: ${files.join(", ") || "(none)"}`);

  const ids: string[] = [];
  const texts: string[] = [];
  const metas: SolutionMetadata[] = [];

  for (const f of files) {
    const kb = JSON.parse(readFileSync(join(dir, f), "utf-8")) as ExamKB;
    const slug = f.replace(/\.json$/, "");
    for (const e of Object.values(kb.questions)) {
      ids.push(`sol:${slug}:${e.id}`);
      texts.push(embedText(e));
      metas.push({
        kind: "solution",
        entryId: e.id,
        points: e.points,
        topic: e.topic ?? "",
        problem: e.problem,
        official_solution: e.official_solution,
        final_answer: e.final_answer ?? "",
        notes: e.notes ?? "",
        exam: kb.exam,
        course: kb.course,
      });
    }
  }

  if (ids.length === 0) {
    console.error("No KB entries found.");
    process.exit(1);
  }
  console.log(`Embedding ${ids.length} entr${ids.length === 1 ? "y" : "ies"}…`);
  const vectors = await embed(texts);

  console.log(`Ensuring index "${PINECONE_INDEX}"…`);
  await ensureIndex();

  const records = ids.map((id, i) => ({ id, values: vectors[i], metadata: metas[i] }));
  await kbIndex().upsert({ records }); // v8: upsert takes { records }, not a bare array
  console.log(`Upserted ${records.length} vectors:`);
  records.forEach((r) => console.log(`  - ${r.id}  (${r.metadata.exam} · ${r.metadata.points} pts)`));

  // Give the serverless index a moment, then report the count.
  await new Promise((r) => setTimeout(r, 3000));
  const stats = await kbIndex().describeIndexStats();
  console.log("index total vectors:", stats.totalRecordCount);
}

main().catch((e) => {
  console.error("INDEX FAILED:", e);
  process.exit(1);
});
