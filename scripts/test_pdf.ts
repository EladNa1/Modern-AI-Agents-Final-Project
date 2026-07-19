// Dev harness (Phase 6) — verifies the PDF -> per-page PNG renderer, no LLM call.
// Run: node --import tsx scripts/test_pdf.ts "Data/.../some exam.pdf" [outDir]
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { basename, join } from "node:path";
import { renderPdfToImages } from "../lib/agent/pdf";

async function main() {
  const pdfPath = process.argv[2];
  if (!pdfPath) {
    console.error('usage: node --import tsx scripts/test_pdf.ts "<file.pdf>" [outDir]');
    process.exit(1);
  }
  const outDir = process.argv[3] ?? "scratch_pdf_pages";

  const buf = readFileSync(pdfPath);
  console.log(`PDF: ${basename(pdfPath)} (${Math.round(buf.length / 1024)} KB)`);

  const { images, pageCount, rendered } = await renderPdfToImages(new Uint8Array(buf));
  console.log(`pages: ${pageCount} · rendered: ${rendered}`);

  mkdirSync(outDir, { recursive: true });
  images.forEach((img, i) => {
    const b64 = img.dataUrl.split(",")[1];
    const bytes = Buffer.from(b64, "base64");
    // PNG magic: 89 50 4E 47
    const ok = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
    const p = join(outDir, `page_${String(i + 1).padStart(2, "0")}.png`);
    writeFileSync(p, bytes);
    console.log(`  page ${i + 1}: ${Math.round(bytes.length / 1024)} KB · PNG=${ok} · ${p}`);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
