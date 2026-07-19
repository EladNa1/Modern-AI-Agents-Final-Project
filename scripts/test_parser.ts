// Dev harness (Phase 1) — runs the real Parser on a scan and prints the result.
// Run: node --env-file=.env.local --import tsx scripts/test_parser.ts [imagePath]
// This file is dev-only (scripts/ is .vercelignored) and not part of the app.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { StepLog } from "../lib/llm";
import { runParser } from "../lib/agent/parser";

async function main() {
  const imgPath =
    process.argv[2] ?? join(process.cwd(), "public", "exam_q3c_scan.png");
  const buf = readFileSync(imgPath);
  const ext = imgPath.toLowerCase().endsWith(".png") ? "png" : "jpeg";
  const dataUrl = `data:image/${ext};base64,${buf.toString("base64")}`;
  console.log(`Image: ${imgPath} (${Math.round(buf.length / 1024)} KB)`);

  const log = new StepLog();
  const res = await runParser([{ dataUrl, detail: "high" }], log);

  console.log("\n=== PARSED QUESTIONS ===");
  for (const q of res.questions) {
    console.log(`\n[${q.id}] confidence=${q.confidence}`);
    console.log(q.text);
    if (q.latex) console.log("LaTeX: " + q.latex);
  }
  console.log("\n=== USAGE (tokens) ===", res.usage);
  console.log(
    "\n=== STEP[0] (trace entry) ===\n" +
      JSON.stringify(
        {
          module: log.steps[0]?.module,
          pattern: log.steps[0]?.pattern,
          prompt_keys: Object.keys(log.steps[0]?.prompt ?? {}),
        },
        null,
        2
      )
  );
  if (res.questions.length === 0) {
    console.log("\n=== RAW REPLY (no JSON parsed) ===\n" + res.raw.slice(0, 1200));
  }
}

main().catch((e) => {
  console.error("PARSER RUN FAILED:", e);
  process.exit(1);
});
