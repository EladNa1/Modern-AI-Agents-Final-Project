// Dev harness (Phase 4) — runs the full orchestrator (incl. Reflector loop).
// Accepts an image or a PDF (PDF takes the same render path as /api/execute).
// Run: node --env-file=.env.local --import tsx scripts/test_agent.ts [path]
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runAgent } from "../lib/agent/orchestrator";
import { renderPdfToImages } from "../lib/agent/pdf";
import type { ImageInput } from "../lib/llm";

async function main() {
  const path =
    process.argv[2] ?? join(process.cwd(), "public", "exam_q3c_scan.png");
  const buf = readFileSync(path);
  const name = path.split(/[\\/]/).pop()!;
  const kb = Math.round(buf.length / 1024);

  let images: ImageInput[];
  let sourceLabel: string;
  if (path.toLowerCase().endsWith(".pdf")) {
    const { images: pages, pageCount, rendered } = await renderPdfToImages(buf);
    images = pages;
    sourceLabel =
      rendered < pageCount
        ? `${name} (${kb} KB, ${rendered}/${pageCount} pages)`
        : `${name} (${kb} KB, ${pageCount} pages)`;
    console.log(`rendered ${rendered}/${pageCount} PDF pages`);
  } else {
    const ext = path.toLowerCase().endsWith(".png") ? "png" : "jpeg";
    images = [
      { dataUrl: `data:image/${ext};base64,${buf.toString("base64")}`, detail: "high" },
    ];
    sourceLabel = `${name} (${kb} KB)`;
  }

  const t0 = Date.now();
  // Optional 2nd arg: exam label to scope retrieval to (e.g. "2025s final A").
  const exam = process.argv[3] || undefined;
  const result = await runAgent({ images, instructions: "", sourceLabel, exam });
  console.log(`elapsed: ${((Date.now() - t0) / 1000).toFixed(1)}s`);

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
