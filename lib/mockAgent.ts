// CheckMate — mock agent.
// Produces a correctly-shaped /api/execute payload with a realistic steps[] trace.
// No real LLM / OCR yet. Module names match the deck architecture (fixed pipeline):
//   Reader (OCR) -> Retriever (rubric match) -> Grader (score+feedback) -> Reflector (conditional)
// Reflection runs only when a grade is shaky and picks one action:
//   APPROVE · REVISE (<=1) · RETRY_OCR (<=1) · ESCALATE (send to teacher).

export type Step = {
  module: string;
  pattern?: string;
  prompt: { system_prompt: string; user_prompt: string };
  response: unknown;
};

export type QuestionResult = {
  id: string;
  title: string;
  score: number;
  max: number;
  status: "ok" | "partial" | "escalate";
  mark: string; // short margin stamp, e.g. "✓" / "3/5"
  feedback: string;
};

export type ExecuteResult = {
  status: "ok" | "error";
  error: string | null;
  response: string | null;
  steps: Step[];
  meta?: {
    total: number;
    max: number;
    questions: QuestionResult[];
    source: string;
    mode: "full" | "subset" | "general";
    instructions: string;
  };
};

const clip = (s: string, n = 600) =>
  s.length > n ? s.slice(0, n) + " …[truncated]" : s;

// A fixed sample exam graded question-by-question. Stands in for the real pipeline.
const SAMPLE_QUESTIONS: QuestionResult[] = [
  {
    id: "Q1",
    title: "Q1 · Derivative — product rule",
    score: 3,
    max: 5,
    status: "partial",
    mark: "3/5",
    feedback:
      "Product rule applied to the first term correctly. Missing the second term x²·(1/x)=x. Method sound, one term omitted → partial credit. Expected 2x·ln x + x.",
  },
  {
    id: "Q2",
    title: "Q2 · Limit — L'Hôpital",
    score: 5,
    max: 5,
    status: "ok",
    mark: "✓",
    feedback:
      "Indeterminate form 0/0 identified, L'Hôpital applied once, limit = 1. Matches the rubric solution. Full credit.",
  },
  {
    id: "Q3",
    title: "Q3 · Lagrange MVT — proof",
    score: 10,
    max: 12,
    status: "partial",
    mark: "10/12",
    feedback:
      "Auxiliary function g(x) constructed correctly and Rolle's theorem invoked. Endpoints g(a)=g(b)=0 shown. Gap: continuity/differentiability of g not stated before applying Rolle. −2.",
  },
  {
    id: "Q4",
    title: "Q4 · Integral — substitution",
    score: 7,
    max: 7,
    status: "ok",
    mark: "✓",
    feedback:
      "u-substitution u=x²+1 chosen, bounds transformed, antiderivative correct against the rubric solution. Full credit.",
  },
  {
    id: "Q5",
    title: "Q5 · Taylor series — remainder",
    score: 4,
    max: 5,
    status: "escalate",
    mark: "?",
    feedback:
      "Handwriting on the remainder bound is ambiguous (two overwritten symbols). Reader confidence low; escalated to the teacher rather than guessed.",
  },
];

function buildSteps(source: string, qs: QuestionResult[], instructions: string): Step[] {
  const steps: Step[] = [];
  const instrLine = instructions ? `\nUser instructions: ${clip(instructions, 200)}` : "";

  // 1) Reader — one OCR pass over all pages, splits the exam into questions.
  steps.push({
    module: "Reader",
    pattern: "OCR",
    prompt: {
      system_prompt:
        "Read all pages of the scanned Calculus 1 exam. Return each question's handwritten work as text/LaTeX, with a confidence score per question. Do not grade.",
      user_prompt: `[exam file: ${clip(source, 120)} — pages 1–4]${instrLine}`,
    },
    response: {
      questions: qs.map((q) => ({
        id: q.id,
        confidence: q.status === "escalate" ? 0.58 : 0.94,
      })),
    },
  });

  // Per question (graded in parallel in the real pipeline): Retriever -> Grader -> Reflector(conditional)
  for (const q of qs) {
    steps.push({
      module: "Retriever",
      pattern: "RAG",
      prompt: {
        system_prompt:
          "Match this question to the knowledge base: pull the official solution, rubric, and graded examples.",
        user_prompt: `${q.id}: ${q.title}`,
      },
      response: { matched: `rubric/${q.id}`, graded_examples: 2, max_points: q.max },
    });

    steps.push({
      module: "Grader",
      pattern: "Few-shot",
      prompt: {
        system_prompt:
          "Grade the student's OWN method against the retrieved rubric and graded examples. Award partial credit. Return score, max, and written feedback.",
        user_prompt: `Grade ${q.id} from OCR text + retrieved rubric.`,
      },
      response: { score: q.score, max: q.max, feedback: clip(q.feedback, 300) },
    });

    // Reflection is conditional — only when the grade looks shaky.
    if (q.status === "partial" || q.status === "escalate") {
      const action = q.status === "escalate" ? "ESCALATE" : "APPROVE";
      steps.push({
        module: "Reflector",
        pattern: "Reflection",
        prompt: {
          system_prompt:
            "The grade looks shaky (borderline partial credit, or low OCR confidence, or score/feedback disagree). Critique it and choose exactly ONE action: APPROVE · REVISE(≤1) · RETRY_OCR(≤1) · ESCALATE.",
          user_prompt: `Review ${q.id}: ${q.score}/${q.max}, OCR confidence ${
            q.status === "escalate" ? 0.58 : 0.94
          }.`,
        },
        response: {
          action,
          note:
            action === "ESCALATE"
              ? "OCR confidence 0.58 on the remainder bound — send to teacher."
              : "Partial credit is justified by the rubric; accept.",
        },
      });
    }
  }

  return steps;
}

// Detect "general feedback, no scoring" requests (EN + HE).
function isGeneral(lc: string): boolean {
  return /general|overall|holistic|no scor|no grad|without grad|do ?n['’]?t grade|feedback only|כללי|חוו|בלי ציון|בלי לצ|ללא ציון|בלי להתמ/.test(
    lc
  );
}

// Extract requested question numbers, e.g. "Q3", "question 4", "שאלה 3".
function requestedQuestions(instr: string): string[] {
  const ids = new Set<string>();
  const re = /(?:q|question|שאל\w*)\s*\.?\s*(\d+)/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(instr))) ids.add("Q" + m[1]);
  return [...ids];
}

export function runMockAgent(source: string, instructions = ""): ExecuteResult {
  if (!source || !source.trim()) {
    return {
      status: "error",
      error: "No exam provided. Upload a scanned exam (PDF, Word, or image).",
      response: null,
      steps: [],
    };
  }

  const instr = (instructions || "").trim();
  const lc = instr.toLowerCase();

  // ---- General-feedback mode: holistic opinion, no per-question scoring. ----
  if (instr && isGeneral(lc)) {
    const steps: Step[] = [
      {
        module: "Reader",
        pattern: "OCR",
        prompt: {
          system_prompt:
            "Read all pages of the scanned Calculus 1 exam and return the student's work as text/LaTeX. Do not grade.",
          user_prompt: `[exam file: ${clip(source, 120)}]\nUser instructions: ${clip(instr, 200)}`,
        },
        response: { questions_read: SAMPLE_QUESTIONS.length },
      },
      {
        module: "Grader",
        pattern: "Few-shot",
        prompt: {
          system_prompt:
            "The user asked for a GENERAL opinion of the student's exam — no per-question scores. Write a short holistic assessment: overall command of the material, strengths, recurring weaknesses, and one suggestion. Do not output numeric grades.",
          user_prompt: `Give a general assessment of the exam.\nUser instructions: ${clip(instr, 200)}`,
        },
        response: {
          assessment:
            "Solid grasp of core techniques (product rule, L'Hôpital, substitution). Main weakness: stating theorem hypotheses before applying them, and completing multi-term derivatives. One answer's handwriting is unclear and would need a second look.",
        },
      },
    ];
    return {
      status: "ok",
      error: null,
      response:
        "General assessment (no scoring, as requested):\n\n" +
        "The student shows a solid command of the core Calculus 1 techniques — the product rule, L'Hôpital's rule, and u-substitution are applied correctly. " +
        "The recurring weakness is rigor around theorem hypotheses (e.g. stating continuity/differentiability before invoking Rolle's/Lagrange's theorem) and occasionally dropping a term in multi-term derivatives. " +
        "One answer's handwriting is ambiguous and would benefit from a closer read. Overall: a capable student who would gain the most from tightening proof rigor.\n\n" +
        "Note: demo build — grading is simulated.",
      steps,
      meta: { total: 0, max: 0, questions: [], source, mode: "general", instructions: instr },
    };
  }

  // ---- Full or subset grading. ----
  const wanted = requestedQuestions(instr);
  const subset = wanted.length > 0;
  const qs = subset
    ? SAMPLE_QUESTIONS.filter((q) => wanted.includes(q.id))
    : SAMPLE_QUESTIONS;

  // Requested a question we don't have in the sample exam.
  if (subset && qs.length === 0) {
    return {
      status: "error",
      error: `Requested ${wanted.join(", ")}, but the exam has ${SAMPLE_QUESTIONS.map((q) => q.id).join(", ")}.`,
      response: null,
      steps: [],
    };
  }

  const steps = buildSteps(source, qs, instr);
  const total = qs.reduce((s, q) => s + q.score, 0);
  const max = qs.reduce((s, q) => s + q.max, 0);
  const escalated = qs.filter((q) => q.status === "escalate");

  const header = subset
    ? `CheckMate graded ${qs.map((q) => q.id).join(", ")}: ${total} / ${max}.`
    : `CheckMate graded the exam: ${total} / ${max}.`;

  const lines = [
    header,
    "",
    ...qs.map((q) => `${q.id} — ${q.score}/${q.max}: ${q.feedback}`),
    "",
    escalated.length
      ? `⚠ ${escalated.length} question(s) escalated to the teacher: ${escalated
          .map((q) => q.id)
          .join(", ")}.`
      : "No questions required human review.",
    "",
    "Note: demo build — grading is simulated. The real pipeline runs OCR, Pinecone RAG, and gpt-5.4-mini grading, and returns the annotated exam.",
  ];

  return {
    status: "ok",
    error: null,
    response: lines.join("\n"),
    steps,
    meta: { total, max, questions: qs, source, mode: subset ? "subset" : "full", instructions: instr },
  };
}
