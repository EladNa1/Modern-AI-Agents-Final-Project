// Dev harness (Phase 2/7) — verifies Retriever matching.
// With PINECONE_API_KEY set, semantic rows also hit Pinecone (needs the KB indexed).
// Run: node --env-file=.env.local --import tsx scripts/test_retriever.ts
import { StepLog } from "../lib/llm";
import { retrieve } from "../lib/agent/retriever";

async function show(label: string, id: string, text: string) {
  const log = new StepLog();
  const r = await retrieve(id, text, log);
  console.log(`\n[${label}] id="${id}"`);
  console.log("  matched:", r ? `${r.entry.id} (${r.exam}, ${r.entry.points} pts, ans=${r.entry.final_answer})` : "NONE");
  console.log("  step.response:", JSON.stringify(log.steps[0].response));
}

async function main() {
  await show("exact id", "Q3c", "");
  await show("hebrew part", "3ג", "");
  await show("wrong id, semantic match", "Q5c", "lim x->0- of integral sin(sqrt t) dt over x^9");
  await show("no match", "Q99", "unrelated question about matrices");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
