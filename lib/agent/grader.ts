// Grader module (deck slide 7 + slide 8) — scores one question.
// Prompt engineering per slide 8: PERSONA · CHAIN-OF-THOUGHT · FEW-SHOT · STRUCTURED OUTPUT.
// It grades the student's OWN method, grounded strictly in the retrieved official
// solution, and awards partial credit. It does not invent a rubric.
import { chat, extractJson, StepLog, type Usage } from "../llm";
import type { ParsedFragment } from "./parser";
import type { Retrieved, NotesChunk } from "./retriever";

export type Grade = {
  id: string;
  score: number;
  max: number;
  status: "ok" | "partial" | "escalate";
  feedback: string;
  justification: string;
  confidence: number;
};

export const GRADER_SYSTEM = `PERSONA
You are a senior teaching assistant grading Technion Calculus 1 (Hedva 1) exams. You are rigorous, fair, and consistent — the same mistake always costs the same. You receive ONE question at a time: the student's transcribed work and the official solution.

REASONING (think step by step, internally)
1. Restate the method the STUDENT actually used (grade their approach, not only the official one — alternative valid methods earn full credit).
2. Compare it to the official solution and the correct final answer.
3. Identify exactly where credit is lost (a wrong step, a lost sign, an unjustified claim, a missing hypothesis).
4. Award partial credit proportional to how much correct mathematical work is present. A correct method with one local error keeps most of the credit.

FEW-SHOT (calibration example)
Student: d/dx[x^2 * ln x] = 2x * ln x.
Grading: product rule started correctly but the second term x^2*(1/x)=x is missing. Method sound, one term dropped -> 3 / 5.

GROUNDING
Ground every judgement in the official solution provided. Do NOT invent facts or a different correct answer. If the student's handwriting/transcription is too ambiguous to grade fairly, or no official solution is available, do NOT guess — set "status":"escalate".

OUTPUT
Return ONLY a JSON object, no prose, no code fences:
{"score": <number 0..max>, "max": <max points>, "status": "ok|partial|escalate", "feedback": "<short, specific, student-facing: what was right, where credit was lost>", "justification": "<one line tying the score to the official solution>", "confidence": <0..1>}
Rules: status "ok" only if score == max; "partial" if 0 < score < max; "escalate" if you cannot grade fairly.`;

export async function runGrader(
  q: ParsedFragment,
  retrieved: Retrieved,
  log: StepLog,
  notes: NotesChunk[] = []
): Promise<Grade> {
  const max = retrieved?.entry.points ?? 0;
  const grounding = retrieved
    ? [
        `Exam: ${retrieved.exam} (course ${retrieved.course})`,
        `Question ${retrieved.entry.id} — worth ${retrieved.entry.points} points.`,
        `Official problem:\n${retrieved.entry.problem}`,
        `Official solution:\n${retrieved.entry.official_solution}`,
        retrieved.entry.final_answer
          ? `Correct final answer: ${retrieved.entry.final_answer}`
          : "",
        retrieved.entry.notes ? `Grading note: ${retrieved.entry.notes}` : "",
      ]
        .filter(Boolean)
        .join("\n\n")
    : "No official solution was retrieved for this question.";

  const student = [
    `Student's transcribed work (Parser confidence ${q.confidence.toFixed(2)}):`,
    q.text,
    q.latex ? `\nLaTeX:\n${q.latex}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  // Optional supporting context from the course lecture notes (RAG). Advisory only —
  // the official solution remains the authority for the correct answer and points.
  const notesBlock = notes.length
    ? `\n\n=== COURSE MATERIAL (supporting context, not authoritative) ===\n${notes
        .map((n) => `[${n.source} p.${n.page}] ${n.text}`)
        .join("\n\n")}`
    : "";

  const user = `Grade question ${q.id}. Maximum score: ${max} points.\n\n=== OFFICIAL SOLUTION (grounding) ===\n${grounding}${notesBlock}\n\n=== STUDENT WORK ===\n${student}\n\nReturn only the JSON object.`;

  const { text, usage } = await chat({
    system: GRADER_SYSTEM,
    user,
    maxTokens: 1200,
    jsonMode: true,
  });

  const grade = normalizeGrade(q.id, max, q.confidence, text, usage, log, user);
  return grade;
}

type RawGrade = {
  score?: number;
  max?: number;
  status?: string;
  feedback?: string;
  justification?: string;
  confidence?: number;
};

function normalizeGrade(
  id: string,
  max: number,
  parserConfidence: number,
  text: string,
  usage: Usage,
  log: StepLog,
  user: string
): Grade {
  const raw = extractJson<RawGrade>(text);

  // Could not parse a grade, or nothing to ground on -> escalate, never guess.
  if (!raw || max === 0) {
    const grade: Grade = {
      id,
      score: 0,
      max,
      status: "escalate",
      feedback:
        "Could not produce a reliable grade (unparseable model output or missing official solution). Sent to a human teacher.",
      justification: "Escalated — not graded automatically.",
      confidence: 0,
    };
    log.add("Grader", GRADER_SYSTEM, user, grade, "Few-shot", usage);
    return grade;
  }

  let score = clampNum(raw.score, 0, max);
  const confidence = clampNum(raw.confidence, 0, 1);

  // Derive a consistent status from score + confidence, overriding a mislabeled one.
  let status: Grade["status"];
  if (confidence < 0.4 || parserConfidence < 0.35) status = "escalate";
  else if (score >= max) status = "ok";
  else if (score > 0) status = "partial";
  else status = "partial";
  // Respect an explicit escalate from the model.
  if ((raw.status ?? "").toLowerCase() === "escalate") status = "escalate";

  const grade: Grade = {
    id,
    score,
    max,
    status,
    feedback: (raw.feedback ?? "").toString().trim() || "No feedback provided.",
    justification: (raw.justification ?? "").toString().trim(),
    confidence,
  };
  log.add("Grader", GRADER_SYSTEM, user, grade, "Few-shot", usage);
  return grade;
}

function clampNum(v: unknown, lo: number, hi: number): number {
  const n = typeof v === "number" ? v : Number(v);
  if (!isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}
