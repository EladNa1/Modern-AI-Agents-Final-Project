// Reflector module (deck slide 7) — the self-critique stage that makes CheckMate
// a Reflection Agent. It critiques a proposed grade against the retrieved official
// solution and chooses ONE action: APPROVE · REVISE (corrected grade) · ESCALATE.
// The orchestrator applies revisions up to N passes, then approves or escalates.
import { chat, extractJson, StepLog, type Usage } from "../llm";
import type { ParsedQuestion } from "./parser";
import type { Retrieved } from "./retriever";
import type { Grade } from "./grader";

export type ReflectAction = "APPROVE" | "REVISE" | "ESCALATE";

export type Reflection = {
  action: ReflectAction;
  score: number | null; // revised score when REVISE
  feedback: string; // revised feedback when REVISE
  note: string; // one-line rationale
  confidence: number;
};

export const REFLECTOR_SYSTEM = `You are the Reflector module of CheckMate, grading Technion Calculus 1 exams. You receive a PROPOSED grade for one question, the student's transcribed work, and the official solution. Critique the proposed grade against that evidence — do not re-grade from scratch, judge whether the grade is fair and grounded.

Check:
- Is the score justified by the official solution and consistent partial-credit reasoning?
- Is credit for the student's OWN valid method preserved (alternative correct methods deserve full credit)?
- Is the student's work too ambiguous/illegible to grade fairly?

Choose EXACTLY ONE action:
- APPROVE — the grade is fair and grounded; keep it.
- REVISE — the grade is wrong or unfair; give a corrected score and feedback.
- ESCALATE — the work cannot be graded fairly (ambiguous handwriting or genuine uncertainty); send to a human teacher. Never guess.

Output ONLY a JSON object, no prose, no code fences:
{"action":"APPROVE|REVISE|ESCALATE","score":<number 0..max, or null>,"feedback":"<corrected student-facing feedback if REVISE, else empty>","note":"<one line: why>","confidence":<0..1>}`;

export async function runReflector(
  q: ParsedQuestion,
  grade: Grade,
  retrieved: Retrieved,
  log: StepLog
): Promise<Reflection> {
  const grounding = retrieved
    ? `Official solution:\n${retrieved.entry.official_solution}\n\nCorrect final answer: ${
        retrieved.entry.final_answer ?? "n/a"
      }\n${retrieved.entry.notes ? "Grading note: " + retrieved.entry.notes : ""}`
    : "No official solution was retrieved.";

  const user = `Question ${q.id} (max ${grade.max} points).

=== PROPOSED GRADE ===
score: ${grade.score}/${grade.max} (status ${grade.status}, confidence ${grade.confidence})
feedback: ${grade.feedback}
justification: ${grade.justification}

=== OFFICIAL SOLUTION (evidence) ===
${grounding}

=== STUDENT WORK (Parser confidence ${q.confidence.toFixed(2)}) ===
${q.text}${q.latex ? "\nLaTeX:\n" + q.latex : ""}

Critique the proposed grade and return only the JSON object.`;

  const { text, usage } = await chat({
    system: REFLECTOR_SYSTEM,
    user,
    maxTokens: 900,
    jsonMode: true,
  });

  const reflection = normalizeReflection(text, grade.max, usage, log, user);
  return reflection;
}

type RawReflection = {
  action?: string;
  score?: number | null;
  feedback?: string;
  note?: string;
  confidence?: number;
};

function normalizeReflection(
  text: string,
  max: number,
  usage: Usage,
  log: StepLog,
  user: string
): Reflection {
  const raw = extractJson<RawReflection>(text);
  const actionRaw = (raw?.action ?? "").toString().toUpperCase();
  const action: ReflectAction =
    actionRaw === "REVISE" || actionRaw === "ESCALATE" ? actionRaw : "APPROVE";

  const scoreNum =
    typeof raw?.score === "number" ? Math.max(0, Math.min(max, raw!.score!)) : null;

  const reflection: Reflection = {
    action,
    score: action === "REVISE" ? scoreNum : null,
    feedback: (raw?.feedback ?? "").toString().trim(),
    note: (raw?.note ?? "").toString().trim() || "(no note)",
    confidence:
      typeof raw?.confidence === "number"
        ? Math.max(0, Math.min(1, raw!.confidence!))
        : 0.5,
  };

  log.add(
    "Reflector",
    REFLECTOR_SYSTEM,
    user,
    {
      action: reflection.action,
      score: reflection.score,
      note: reflection.note,
      confidence: reflection.confidence,
    },
    "Reflection",
    usage
  );

  return reflection;
}
