// PDF page renderer — turns an uploaded PDF into per-page PNG images so the
// vision Parser can read it one page at a time (the model reads images, not PDF).
// Uses MuPDF (WASM, no native binary) so it runs on Vercel serverless — the same
// engine scripts/ingest_solution.py uses via pymupdf, for consistent output.
import type { ImageInput } from "../llm";

const RENDER_DPI = 150; // enough for handwritten-math OCR; keeps the base64 payload small
const MAX_PAGES = 12; // guard against runaway cost/time on an oversized upload

export type PdfRender = {
  images: ImageInput[]; // one high-detail PNG data URL per rendered page
  pageCount: number; // pages in the document
  rendered: number; // pages actually rendered (min(pageCount, MAX_PAGES))
};

// Render up to MAX_PAGES of a PDF to PNG images. Throws if the bytes are not a
// readable PDF; the caller turns that into a clean 400.
export async function renderPdfToImages(pdf: Uint8Array): Promise<PdfRender> {
  // Dynamic import: mupdf is ESM + WASM — keep it out of the module graph until a
  // PDF actually arrives (image uploads never pay for it).
  const mupdf = await import("mupdf");

  const doc = mupdf.Document.openDocument(pdf, "application/pdf");
  try {
    const pageCount = doc.countPages();
    const rendered = Math.min(pageCount, MAX_PAGES);
    const scale = RENDER_DPI / 72; // PDF user space is 72 dpi
    const matrix = mupdf.Matrix.scale(scale, scale);

    const images: ImageInput[] = [];
    for (let i = 0; i < rendered; i++) {
      const page = doc.loadPage(i);
      const pix = page.toPixmap(matrix, mupdf.ColorSpace.DeviceRGB, false);
      const b64 = Buffer.from(pix.asPNG()).toString("base64");
      images.push({ dataUrl: `data:image/png;base64,${b64}`, detail: "high" });
      pix.destroy();
      page.destroy();
    }
    return { images, pageCount, rendered };
  } finally {
    doc.destroy();
  }
}
