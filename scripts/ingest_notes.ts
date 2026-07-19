// Dev harness (Phase 7) — ingest the full course lecture notes into Pinecone as
// "notes" records for RAG grounding (beyond the official solutions).
// The lectures PDF has clean Unicode text (unlike the exam PDFs), so we extract
// text directly with mupdf — no vision OCR, no LLM cost. Embedding only.
//
// Run: node --env-file=.env.local --import tsx scripts/ingest_notes.ts \
//        "Data/חדוא 1- הרצאות.pdf" "Hedva 1 lecture notes"
import { readFileSync } from "node:fs";
import { embed } from "../lib/llm";
import { hasPinecone } from "../lib/env";
import { ensureIndex, kbIndex, type NotesMetadata } from "../lib/kb/pinecone";

const MIN_CHARS = 40; // skip near-empty pages (covers, blank)
const META_TEXT_CAP = 2000; // chars stored in metadata (returned to the Grader)
const EMBED_CAP = 6000; // chars sent to the embedder per page
const BATCH = 50; // embeddings per API call

async function extractPages(pdfPath: string): Promise<{ page: number; text: string }[]> {
  const mupdf = await import("mupdf");
  const doc = mupdf.Document.openDocument(new Uint8Array(readFileSync(pdfPath)), "application/pdf");
  const out: { page: number; text: string }[] = [];
  try {
    for (let i = 0; i < doc.countPages(); i++) {
      const page = doc.loadPage(i);
      const st = page.toStructuredText();
      const text = st.asText().replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
      st.destroy();
      page.destroy();
      if (text.length >= MIN_CHARS) out.push({ page: i + 1, text });
    }
  } finally {
    doc.destroy();
  }
  return out;
}

async function embedBatched(texts: string[]): Promise<number[][]> {
  const vecs: number[][] = [];
  for (let i = 0; i < texts.length; i += BATCH) {
    const chunk = texts.slice(i, i + BATCH);
    vecs.push(...(await embed(chunk)));
    process.stdout.write(`  embedded ${Math.min(i + BATCH, texts.length)}/${texts.length}\r`);
  }
  process.stdout.write("\n");
  return vecs;
}

async function main() {
  if (!hasPinecone) { console.error("PINECONE_API_KEY not set."); process.exit(1); }
  const pdf = process.argv[2] ?? "Data/חדוא 1- הרצאות.pdf";
  const source = process.argv[3] ?? "Hedva 1 lecture notes";

  console.log(`Extracting text from ${pdf}…`);
  const pages = await extractPages(pdf);
  console.log(`${pages.length} pages with text.`);

  console.log("Embedding…");
  const vectors = await embedBatched(pages.map((p) => p.text.slice(0, EMBED_CAP)));

  console.log(`Ensuring index…`);
  await ensureIndex();

  const records = pages.map((p, i) => ({
    id: `notes:p${String(p.page).padStart(3, "0")}`,
    values: vectors[i],
    metadata: {
      kind: "notes",
      source,
      page: p.page,
      text: p.text.slice(0, META_TEXT_CAP),
    } satisfies NotesMetadata,
  }));

  // Upsert in batches (Pinecone caps request size).
  for (let i = 0; i < records.length; i += 100) {
    await kbIndex().upsert({ records: records.slice(i, i + 100) });
  }
  console.log(`Upserted ${records.length} note chunks.`);

  await new Promise((r) => setTimeout(r, 3000));
  const stats = await kbIndex().describeIndexStats();
  console.log("index total vectors:", stats.totalRecordCount);
}

main().catch((e) => { console.error("NOTES INGEST FAILED:", e); process.exit(1); });
