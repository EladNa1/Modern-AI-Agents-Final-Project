import { NextResponse } from "next/server";
import { runMockAgent } from "@/lib/mockAgent";

export const runtime = "nodejs";
export const maxDuration = 60; // well under Vercel's 300s ceiling

const ALLOWED = [".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"];

export async function POST(req: Request) {
  const ct = req.headers.get("content-type") || "";
  let source = "";
  let instructions = "";

  try {
    if (ct.includes("multipart/form-data")) {
      // Exam upload — the primary path.
      const form = await req.formData();
      const file = form.get("file");
      const instr = form.get("instructions");
      if (typeof instr === "string") instructions = instr;
      if (!file || typeof file === "string") {
        return NextResponse.json(
          { status: "error", error: "No exam file in the upload.", response: null, steps: [] },
          { status: 400 }
        );
      }
      const name = file.name || "exam";
      const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED.includes(ext)) {
        return NextResponse.json(
          {
            status: "error",
            error: `Unsupported file type "${ext}". Accepted: PDF, Word (.doc/.docx), or image (.png/.jpg/.webp).`,
            response: null,
            steps: [],
          },
          { status: 400 }
        );
      }
      source = `${name} (${Math.round(file.size / 1024)} KB)`;
    } else {
      // JSON path — { "prompt": "…", "instructions"?: "…" } — kept for the required contract.
      const body = await req.json();
      source = typeof body?.prompt === "string" ? body.prompt : "";
      if (typeof body?.instructions === "string") instructions = body.instructions;
    }
  } catch {
    return NextResponse.json(
      { status: "error", error: "Could not read the request body.", response: null, steps: [] },
      { status: 400 }
    );
  }

  const result = runMockAgent(source, instructions);
  return NextResponse.json(result, { status: result.status === "error" ? 400 : 200 });
}
