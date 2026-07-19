// Orchestrator — runs the CheckMate Reflection Agent end to end and assembles the
// brief-shaped ExecuteResult. Pipeline (deck slide 7):
//   Parser (once) -> per question: Retriever -> Grader -> Reflector (revise <= N) -> approve|escalate
// Falls back to the mock when there is no API key or no image to read, so the app
// and the required API contract keep working with no secrets configured.
import { StepLog, type ImageInput } from "../llm";
import { hasLLM } from "../env";
import type { ExecuteResult, QuestionResult } from "../types";
import { runParser } from "./parser";
import { retrieve, retrieveNotes } from "./retriever";
import { runGrader, type Grade } from "./grader";
import { runReflector } from "./reflector";
import { runMockAgent } from "../mockAgent";

const MAX_REVISE_PASSES = 2;

export type AgentInput = {
  images: ImageInput[];
  instructions?: string;
  sourceLabel: string; // e.g. "exam.png (186 KB)" — for the response + meta
};

export async function runAgent(input: AgentInput): Promise<ExecuteResult> {
  // Fallback: no key configured or nothing to look at -> deterministic mock.
  if (!hasLLM || input.images.length === 0) {
    return runMockAgent(input.sourceLabel, input.instructions ?? "");
  }

  const instructions = (input.instructions ?? "").trim();
  const log = new StepLog();

  try {
    const { questions } = await runParser(input.images, log, instructions);

    if (questions.length === 0) {
      return {
        status: "error",
        error:
          "The Parser could not read any question from the scan. Try a clearer image.",
        response: null,
        steps: log.steps,
      };
    }

    const results: QuestionResult[] = [];
    for (const q of questions) {
      const query = `${q.text} ${q.latex ?? ""}`;
      const retrieved = await retrieve(q.id, query, log);
      const notes = await retrieveNotes(query, log);
      let grade = await runGrader(q, retrieved, log, notes);

      // Reflection loop: critique, revise up to N passes, then approve or escalate.
      for (let pass = 0; pass < MAX_REVISE_PASSES; pass++) {
        const refl = await runReflector(q, grade, retrieved, log);
        if (refl.action === "APPROVE") break;
        if (refl.action === "ESCALATE") {
          grade = { ...grade, status: "escalate" };
          break;
        }
        // REVISE — apply the corrected grade and reflect again.
        grade = applyRevision(grade, refl.score, refl.feedback);
      }

      results.push(toQuestionResult(q.id, grade, retrieved));
    }

    return assemble(results, input.sourceLabel, instructions, log);
  } catch (err) {
    // Any gateway/parse failure -> a valid error payload (never throw to the route).
    return {
      status: "error",
      error:
        "The agent failed while grading: " +
        (err instanceof Error ? err.message : String(err)),
      response: null,
      steps: log.steps,
    };
  }
}

function applyRevision(
  grade: Grade,
  newScore: number | null,
  newFeedback: string
): Grade {
  const score = newScore != null ? Math.max(0, Math.min(grade.max, newScore)) : grade.score;
  const status: Grade["status"] =
    score >= grade.max ? "ok" : "partial";
  return {
    ...grade,
    score,
    status,
    feedback: newFeedback.trim() ? newFeedback.trim() : grade.feedback,
  };
}

function toQuestionResult(
  id: string,
  grade: Grade,
  retrieved: Awaited<ReturnType<typeof retrieve>>
): QuestionResult {
  // Prefer the knowledge base's canonical id — the Parser's guess of the question
  // number is unreliable when the scan shows only one part (e.g. just "(ג)").
  const displayId = retrieved?.entry.id ?? id;
  const topic = retrieved?.entry.topic;
  const title = topic ? `${displayId} · ${topic}` : displayId;
  const mark =
    grade.status === "ok" ? "✓" : grade.status === "escalate" ? "?" : `${grade.score}/${grade.max}`;
  return {
    id: displayId,
    title,
    score: grade.score,
    max: grade.max,
    status: grade.status,
    mark,
    feedback: grade.feedback,
  };
}

function assemble(
  questions: QuestionResult[],
  source: string,
  instructions: string,
  log: StepLog
): ExecuteResult {
  const total = questions.reduce((s, q) => s + q.score, 0);
  const max = questions.reduce((s, q) => s + q.max, 0);
  const escalated = questions.filter((q) => q.status === "escalate");

  const lines = [
    `CheckMate graded ${questions.map((q) => q.id).join(", ")}: ${total} / ${max}.`,
    "",
    ...questions.map((q) => `${q.id} — ${q.score}/${q.max}: ${q.feedback}`),
    "",
    escalated.length
      ? `⚠ ${escalated.length} question(s) escalated to the teacher: ${escalated
          .map((q) => q.id)
          .join(", ")}.`
      : "No questions required human review.",
  ];

  return {
    status: "ok",
    error: null,
    response: lines.join("\n"),
    steps: log.steps,
    meta: { total, max, questions, source, mode: "full", instructions },
  };
}
