import { NextResponse } from "next/server";
import { runMockAgent } from "@/lib/mockAgent";

export const runtime = "nodejs";

export async function GET() {
  const exampleSource = "calc1_2024w_final_A.pdf (612 KB)";
  const example = runMockAgent(exampleSource);

  return NextResponse.json({
    description:
      "CheckMate is an autonomous agent that grades Calculus 1 (Hedva 1) exams. You upload a scanned exam (PDF, Word, or images); it reads every page with OCR, splits the exam into questions, and grades each on the student's ACTUAL method — awarding partial credit and writing per-question feedback. Grades are grounded in the retrieved official solution and rubric, not invented. Ambiguous handwriting or low-confidence grades are escalated to a human teacher, never silently guessed. It returns the graded exam with annotations plus the total. What it CANNOT do: it does not set exam questions, does not tutor, and does not fabricate a rubric.",
    purpose:
      "Cut the hours TAs spend grading Calculus 1 exam stacks by hand, and give every student instant, consistent, fair feedback on every question.",
    prompt_template: {
      template:
        "Upload a scanned Calculus 1 exam file (PDF / Word / image) to POST /api/execute as multipart form-data under the field `file`. No free-text prompt is required. (A JSON body { \"prompt\": \"<exam reference>\" } is also accepted.)",
    },
    prompt_examples: [
      {
        prompt: `POST /api/execute  (multipart)  file=${exampleSource}`,
        full_response: example.response,
        steps: example.steps,
      },
    ],
  });
}
