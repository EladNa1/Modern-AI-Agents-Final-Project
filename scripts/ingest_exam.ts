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
import { extractExamStructure } from "../lib/kb/examStructure";

type Entry = {
  id: string; points: number; topic: string; problem: string;
  official_solution: string; final_answer: string; notes: string;
};

const SYSTEM = `You are building a grading knowledge base for a Technion Calculus exam.
You receive ONE page of the official SOLUTION document (it restates each question and gives the official answer/worked solution).
Extract every exam question or sub-part visible on this page. Do NOT invent questions not shown.

CRITICAL — multiple-choice answer options are NOT sub-parts.
In a multiple-choice question, the Hebrew letters (א)(ב)(ג)(ד) introduce the CANDIDATE ANSWERS to choose between. They are one single question, not four.
Emit exactly ONE entry for such a question (id "Q1", never "Q1a"/"Q1b"/…), list the options inside "problem", and put the correct option in "final_answer".
Only treat a Hebrew letter as a real sub-part when it states its own task to perform — it starts with an instruction verb such as חשבו / חשב / הוכיחו / הוכח / מצאו / מצא / נסחו / קבעו, and usually carries its own point value.
If you are unsure, ask: does this letter give something to SOLVE (sub-part) or something to PICK (option)? Picking means it is an option.

POINTS are supplied with the id list — copy the value given for that id. Do not compute or guess one.

QUESTION IDS ARE GIVEN TO YOU.
The user message lists every question id on this exam, read directly from the document. Use ONLY ids from that list — never invent one, never renumber, never restart at 1.
A long question runs over several pages and the later pages often show only a bare sub-part letter, e.g. "(ב) (10 נקודות) חשבו…". Match it to the right id from the list using the surrounding content.
If a page shows something that matches no id on the list, leave it out.

For each question output:
- id: "Q<number><part>" using the question number and, for a REAL sub-part only, the Hebrew letter mapped to a/b/c/d/e (e.g. שאלה 3 סעיף ג -> "Q3c"; a question with no sub-parts -> "Q3").
- points: integer as described above, else 0.
- topic: a short English topic label (e.g. "improper limit via FTC + L'Hopital").
- problem: the full question statement, Hebrew preserved, math in LaTeX. For multiple choice, include all the options.
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
  const bytes = new Uint8Array(readFileSync(pdf));

  // Structure pass first: ids and point values come from the PDF's own text layer,
  // never from the vision model, because a wrong point value silently corrupts every
  // grade computed against it.
  const structure = await extractExamStructure(bytes);
  const skeleton = new Map(structure.questions.map((q) => [normId(q.id), q]));
  if (skeleton.size === 0) {
    console.log("WARNING — no text layer structure found; falling back to vision-extracted ids/points.");
  } else {
    console.log(
      `structure: ${structure.questions.map((q) => `${q.id}=${q.points}`).join(", ")}  → total ${structure.total}` +
        (structure.total === 100 ? " ✓" : "  ← expected 100")
    );
  }
  const idList = structure.questions.map((q) => `${q.id} (${q.points} pts)`).join(", ");

  const { images, pageCount, rendered } = await renderPdfToImages(bytes);
  console.log(`${examLabel}: ${pageCount} pages, extracting ${rendered}…`);

  const merged: Record<string, Entry> = {};
  const usage = { prompt: 0, completion: 0, total: 0 };

  for (let p = 0; p < images.length; p++) {
    const onThisPage = structure.idsByPage.get(p + 1) ?? [];
    const context = idList
      ? `The questions on this exam are: ${idList}. Use only these ids.` +
        (onThisPage.length
          ? ` This page shows: ${onThisPage.join(", ")} — label what you extract with exactly those ids.`
          : ` This page shows no question marker; if it continues a previous question, return nothing.`)
      : `No id list is available for this exam — infer ids from the page.`;
    const { text, usage: u } = await chat({
      system: SYSTEM,
      user: `Extract the questions on this solution page (page ${p + 1} of ${rendered}). ${context} Return only the JSON object.`,
      images: [images[p]],
      maxTokens: 3000,
      jsonMode: true,
    });
    usage.prompt += u.prompt; usage.completion += u.completion; usage.total += u.total;
    const pageQs = extractJson<{ questions?: Record<string, Entry> }>(text)?.questions ?? {};

    let skipped = 0;
    for (const e of Object.values(pageQs)) {
      if (!e || !e.id || (!e.problem && !e.official_solution)) continue;
      const key = normId(e.id);
      // With a skeleton in hand, anything the model invented is dropped rather than
      // silently added to the knowledge base.
      const known = skeleton.get(key);
      if (skeleton.size > 0 && !known) { skipped++; continue; }
      const points = known ? known.points : e.points || 0;
      const ex = merged[key];
      if (!ex) {
        merged[key] = {
          id: known ? known.id : e.id, points, topic: e.topic || "",
          problem: e.problem || "", official_solution: e.official_solution || "",
          final_answer: e.final_answer || "", notes: e.notes || "",
        };
      } else {
        // continuation on a later page
        ex.problem = [ex.problem, e.problem].filter(Boolean).join("\n");
        ex.official_solution = [ex.official_solution, e.official_solution].filter(Boolean).join("\n");
        if (!ex.final_answer && e.final_answer) ex.final_answer = e.final_answer;
        if (!ex.topic && e.topic) ex.topic = e.topic;
      }
    }
    console.log(
      `  page ${p + 1}: +${Object.keys(pageQs).length}${skipped ? ` (−${skipped} off-skeleton)` : ""} → ${Object.keys(merged).length} total`
    );
  }

  // Anything in the skeleton the vision pass never described is a real gap: the entry
  // would carry points but no solution to grade against.
  const missing = [...skeleton.keys()].filter((k) => !merged[k]).map((k) => skeleton.get(k)!.id);
  const zeroed = Object.values(merged).filter((e) => !e.points).map((e) => e.id);
  const total = Object.values(merged).reduce((s, e) => s + e.points, 0);

  const out = { exam: examLabel, course, questions: merged };
  const dir = join(process.cwd(), "lib", "kb", "solutions");
  const file = join(dir, `${outSlug}.json`);
  writeFileSync(file, JSON.stringify(out, null, 2), { encoding: "utf-8" });
  console.log(`\nwrote ${file} — ${Object.keys(merged).length} questions`);
  console.log("ids:", Object.values(merged).map((e) => `${e.id}(${e.points})`).join(", "));
  // The exam total is the headline sanity check: a Technion final is out of 100, so
  // anything else means questions were missed, duplicated, or left unweighted.
  console.log(`TOTAL POINTS: ${total}${total === 100 ? " ✓" : "  ← expected 100, review the ingest"}`);
  if (zeroed.length) console.log(`WARNING — ${zeroed.length} question(s) with 0 points: ${zeroed.join(", ")}`);
  if (missing.length) console.log(`WARNING — ${missing.length} question(s) in the exam but not extracted: ${missing.join(", ")}`);
  console.log("tokens:", usage);
}

main().catch((e) => { console.error("INGEST FAILED:", e); process.exit(1); });
