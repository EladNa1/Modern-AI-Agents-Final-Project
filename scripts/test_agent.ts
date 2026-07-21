// Dev harness (Phase 4) — runs the full orchestrator (incl. Reflector loop).
// Run: node --env-file=.env.local --import tsx scripts/test_agent.ts [imagePath]
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runAgent } from "../lib/agent/orchestrator";

async function main() {
  const imgPath =
    process.argv[2] ?? join(process.cwd(), "public", "exam_q3c_scan.png");
  const buf = readFileSync(imgPath);
  const ext = imgPath.toLowerCase().endsWith(".png") ? "png" : "jpeg";
  const dataUrl = `data:image/${ext};base64,${buf.toString("base64")}`;

  const result = await runAgent({
    images: [{ dataUrl, detail: "high" }],
    instructions: "",
    sourceLabel: `${imgPath.split(/[\\/]/).pop()} (${Math.round(buf.length / 1024)} KB)`,
  });

  console.log("status:", result.status, "| error:", result.error);
  console.log("\n=== RESPONSE ===\n" + result.response);
  console.log("\n=== META ===", JSON.stringify(result.meta, null, 2));
  console.log("\n=== STEPS (order + module) ===");
  result.steps.forEach((s, i) =>
    console.log(`  ${i + 1}. ${s.module}${s.pattern ? " · " + s.pattern : ""}`)
  );
  console.log(
    "\nstep schema keys[0]:",
    result.steps[0] ? Object.keys(result.steps[0].prompt) : "none"
  );
}

main().catch((e) => {
  console.error("AGENT RUN FAILED:", e);
  process.exit(1);
});
