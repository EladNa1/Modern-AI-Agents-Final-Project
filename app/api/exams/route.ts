import { NextResponse } from "next/server";
import { examOptions } from "@/lib/kb/exams";

export const runtime = "nodejs";

// The exam picker in the UI reads this: one entry per bundled official-solution exam.
// The teacher selects the exam a scan belongs to so retrieval is scoped to it.
export async function GET() {
  return NextResponse.json({ exams: examOptions() });
}
