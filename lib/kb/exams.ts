// Central registry of the bundled official-solution exams.
//
// Question ids AND topics repeat across exams (every exam has a "Q1", and several share
// near-identical questions such as "state Rolle's theorem"), so retrieval that is not
// scoped to one exam can ground a grade on the wrong exam's question. The teacher tells
// CheckMate which exam a scan belongs to; the Retriever then filters to it. This module
// is the single source of that exam list — kept free of the Pinecone SDK so the API
// route that serves it to the UI picker does not drag the client bundle along.
import exam2023wA from "./solutions/2023w_final_A.json";
import exam2024wA from "./solutions/2024w_final_A.json";
import exam2025wA from "./solutions/2025w_final_A.json";
import exam2025sA from "./solutions/2025s_final_A.json";
import exam2026wB from "./solutions/2026w_final_B.json";

export type SolutionEntry = {
  id: string;
  points: number;
  topic?: string;
  problem: string;
  official_solution: string;
  final_answer?: string;
  notes?: string;
};

export type ExamKB = {
  exam: string; // e.g. "2025s final A" — matches the `exam` field on every Pinecone vector
  course: string; // e.g. "104018"
  questions: Record<string, SolutionEntry>;
};

// Every bundled exam. Add one import + one entry per newly ingested exam.
export const EXAMS: ExamKB[] = [
  exam2023wA,
  exam2024wA,
  exam2025wA,
  exam2025sA,
  exam2026wB,
] as unknown as ExamKB[];

// Compact list for the UI picker and /api/exams. `value` is the exam label stored in
// Pinecone metadata (what the Retriever filters on); `label` is what the teacher reads.
export type ExamOption = { value: string; course: string; label: string };
export function examOptions(): ExamOption[] {
  return EXAMS.map((k) => ({ value: k.exam, course: k.course, label: `${k.exam} · ${k.course}` }));
}
