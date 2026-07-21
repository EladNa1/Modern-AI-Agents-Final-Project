// Dev harness (Phase 7) — ingest a full official-solution PDF into a KB JSON file.
// Renders every page (mupdf), extracts questions PER PAGE via a structured vision
// call (jsonMode), and merges parts across pages. Output feeds scripts/index_kb.ts.
//
// Run: node --env-file=.env.local --import tsx scripts/ingest_exam.ts \
//        "<solution.pdf>" <course> "<exam label>" <outSlug>
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chat, extractJson } from "../lib/llm";
import { renderPdfToImages } from "../lib/agent/pdf";

type Entry = {
  id: string; points: number; topic: string; problem: string;
  official_solution: string; final_answer: string; notes: string;
};

const SYSTEM = `You are building a grading knowledge base for a Technion Calculus 1 (Hedva 1) exam.
You receive ONE page of the official SOLUTION document (it restates each question and gives the official answer/worked solution).
Extract every exam question or sub-part visible on this page. Do NOT invent questions not shown.

For each, output:
- id: "Q<number><part>" using the question number and Hebrew part letter mapped to a/b/c/d/e (e.g. שאלה 3 סעיף ג -> "Q3c"; a whole question with no parts -> "Q3").
- points: integer points if shown (e.g. "9 נקודות" -> 9), else 0.
- topic: a short English topic label (e.g. "improper limit via FTC + L'Hopital").
- problem: the full question statement, Hebrew preserved, math in LaTeX.
- official_solution: the official worked solution / for a true-false or multiple-choice item, state which option is correct and why. Hebrew + LaTeX.
- final_answer: the final result or the correct option (short).
- notes: one key subtlety or common mistake, or "".

Output ONLY JSON, no prose, no code fences:
{"questions":{"Q3c":{"id":"Q3c","points":9,"topic":"...","problem":"...","official_solution":"...","final_answer":"...","notes":"..."}}}`;

const HE: Record<string, string> = { "א": "a", "ב": "b", "ג": "c", "ד": "d", "ה": "e" };
function normId(s: string): string {
  let o = (s ?? "").toLowerCase();
  for (const [h, e] of Object.entries(HE)) o = o.replaceAll(h, e);
  return o.replace(/[^a-z0-9]/g, "").replace(/^q/, "");
}

async function main() {
  const [pdf, course, examLabel, outSlug] = process.argv.slice(2);
  if (!pdf || !course || !examLabel || !outSlug) {
    console.error('usage: ingest_exam.ts "<solution.pdf>" <course> "<exam label>" <outSlug>');
    process.exit(1);
  }
  const { readFileSync } = await import("node:fs");
  const { images, pageCount, rendered } = await renderPdfToImages(new Uint8Array(readFileSync(pdf)));
  console.log(`${examLabel}: ${pageCount} pages, extracting ${rendered}…`);

  const merged: Record<string, Entry> = {};
  const usage = { prompt: 0, completion: 0, total: 0 };

  for (let p = 0; p < images.length; p++) {
    const { text, usage: u } = await chat({
      system: SYSTEM,
      user: `Extract the questions on this solution page (page ${p + 1} of ${rendered}). Return only the JSON object.`,
      images: [images[p]],
      maxTokens: 3000,
      jsonMode: true,
    });
    usage.prompt += u.prompt; usage.completion += u.completion; usage.total += u.total;
    const pageQs = extractJson<{ questions?: Record<string, Entry> }>(text)?.questions ?? {};
    for (const e of Object.values(pageQs)) {
      if (!e || !e.id || (!e.problem && !e.official_solution)) continue;
      const key = normId(e.id);
      const ex = merged[key];
      if (!ex) {
        merged[key] = {
          id: e.id, points: e.points || 0, topic: e.topic || "",
          problem: e.problem || "", official_solution: e.official_solution || "",
          final_answer: e.final_answer || "", notes: e.notes || "",
        };
      } else {
        // continuation on a later page
        ex.problem = [ex.problem, e.problem].filter(Boolean).join("\n");
        ex.official_solution = [ex.official_solution, e.official_solution].filter(Boolean).join("\n");
        ex.points = Math.max(ex.points, e.points || 0);
        if (!ex.final_answer && e.final_answer) ex.final_answer = e.final_answer;
      }
    }
    console.log(`  page ${p + 1}: +${Object.keys(pageQs).length} → ${Object.keys(merged).length} total`);
  }

  const out = { exam: examLabel, course, questions: merged };
  const dir = join(process.cwd(), "lib", "kb", "solutions");
  const file = join(dir, `${outSlug}.json`);
  writeFileSync(file, JSON.stringify(out, null, 2), { encoding: "utf-8" });
  console.log(`\nwrote ${file} — ${Object.keys(merged).length} questions`);
  console.log("ids:", Object.values(merged).map((e) => `${e.id}(${e.points})`).join(", "));
  console.log("tokens:", usage);
}

main().catch((e) => { console.error("INGEST FAILED:", e); process.exit(1); });
