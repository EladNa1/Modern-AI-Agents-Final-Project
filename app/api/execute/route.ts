import { NextResponse } from "next/server";
import { runAgent } from "@/lib/agent/orchestrator";
import { runMockAgent } from "@/lib/mockAgent";
import type { ImageInput } from "@/lib/llm";
import type { ExecuteResult } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 300; // Vercel ceiling; the reflection loop may take a while

const ALLOWED = [".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"];
const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

function bad(error: string): NextResponse {
  const body: ExecuteResult = { status: "error", error, response: null, steps: [] };
  return NextResponse.json(body, { status: 400 });
}

export async function POST(req: Request) {
  const ct = req.headers.get("content-type") || "";

  try {
    if (ct.includes("multipart/form-data")) {
      // Exam upload — the primary path.
      const form = await req.formData();
      const file = form.get("file");
      const instr = form.get("instructions");
      const instructions = typeof instr === "string" ? instr : "";
      const examField = form.get("exam");
      const exam = typeof examField === "string" && examField.trim() ? examField.trim() : undefined;

      if (!file || typeof file === "string") {
        return bad("No exam file in the upload.");
      }
      const name = file.name || "exam";
      const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED.includes(ext)) {
        return bad(
          `Unsupported file type "${ext}". Accepted: PDF, Word (.doc/.docx), or image (.png/.jpg/.webp).`
        );
      }

      let sourceLabel = `${name} (${Math.round(file.size / 1024)} KB)`;

      // Images go straight to the vision Parser. PDFs are rendered to per-page PNGs
      // and fed page by page. Word (.doc/.docx) has no renderer yet, so it takes the
      // mock fallback inside runAgent (images: []).
      const images: ImageInput[] = [];
      if (IMAGE_MIME[ext]) {
        const buf = Buffer.from(await file.arrayBuffer());
        images.push({
          dataUrl: `data:${IMAGE_MIME[ext]};base64,${buf.toString("base64")}`,
          detail: "high",
        });
      } else if (ext === ".pdf") {
        const { renderPdfToImages } = await import("@/lib/agent/pdf");
        const buf = Buffer.from(await file.arrayBuffer());
        let render;
        try {
          render = await renderPdfToImages(new Uint8Array(buf));
        } catch {
          return bad("Could not read the PDF — it may be corrupt or password-protected.");
        }
        if (render.pageCount === 0) return bad("The PDF has no pages to grade.");
        images.push(...render.images);
        sourceLabel =
          render.rendered < render.pageCount
            ? `${name} (${Math.round(file.size / 1024)} KB, ${render.rendered}/${render.pageCount} pages graded)`
            : `${name} (${Math.round(file.size / 1024)} KB, ${render.pageCount} page${render.pageCount > 1 ? "s" : ""})`;
      }

      const result = await runAgent({ images, instructions, sourceLabel, exam });
      return NextResponse.json(result, {
        status: result.status === "error" ? 400 : 200,
      });
    }

    // JSON path — { "prompt": "…", "instructions"?: "…" } — required-contract shape.
    // No scan image here, so the deterministic mock keeps the contract working.
    const body = await req.json();
    const prompt = typeof body?.prompt === "string" ? body.prompt : "";
    const instructions =
      typeof body?.instructions === "string" ? body.instructions : "";
    const result = runMockAgent(prompt, instructions);
    return NextResponse.json(result, {
      status: result.status === "error" ? 400 : 200,
    });
  } catch {
    return bad("Could not read the request body.");
  }
}
