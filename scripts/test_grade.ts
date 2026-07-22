// Dev harness (Phase 3) — full chain Parser -> Retriever -> Grader on one scan.
// Run: node --env-file=.env.local --import tsx scripts/test_grade.ts [imagePath]
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { StepLog } from "../lib/llm";
import { runParser } from "../lib/agent/parser";
import { retrieve } from "../lib/agent/retriever";
import { runGrader } from "../lib/agent/grader";

async function main() {
  const imgPath =
    process.argv[2] ?? join(process.cwd(), "public", "exam_q3c_scan.png");
  // Optional 2nd arg: the exam label to scope retrieval to (e.g. "2026w final B").
  const exam = process.argv[3] || undefined;
  const buf = readFileSync(imgPath);
  const ext = imgPath.toLowerCase().endsWith(".png") ? "png" : "jpeg";
  const dataUrl = `data:image/${ext};base64,${buf.toString("base64")}`;

  const log = new StepLog();

  const { fragments: questions } = await runParser([{ dataUrl, detail: "high" }], log);
  console.log(`Parsed ${questions.length} question(s).`);

  if (exam) console.log(`Scoped to exam: ${exam}`);
  for (const q of questions) {
    const retrieved = await retrieve(q.id, `${q.text} ${q.latex ?? ""}`, log, exam);
    console.log(
      `\nRetrieved for ${q.id}:`,
      retrieved ? `${retrieved.entry.id} (${retrieved.entry.points} pts)` : "NONE"
    );
    const grade = await runGrader(q, retrieved, log);
    console.log(`\n=== GRADE ${grade.id} ===`);
    console.log(`score: ${grade.score}/${grade.max}  status: ${grade.status}  confidence: ${grade.confidence}`);
    console.log(`feedback: ${grade.feedback}`);
    console.log(`justification: ${grade.justification}`);
  }

  console.log("\n=== TRACE (steps[]) ===");
  log.steps.forEach((s, i) =>
    console.log(`  ${i + 1}. ${s.module}${s.pattern ? " · " + s.pattern : ""}`)
  );
  console.log("\n=== USAGE (tokens) ===", log.usage);
}

main().catch((e) => {
  console.error("GRADE RUN FAILED:", e);
  process.exit(1);
});
