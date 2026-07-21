// Shared agent types. The Step shape matches the course-brief contract exactly:
// each logged LLM call is { module, prompt: { System_prompt, User_prompt }, response }.
// Module names must stay consistent with the architecture diagram and agent_info.

export type Step = {
  module: string;
  pattern?: string;
  prompt: { System_prompt: string; User_prompt: string };
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

// Brief requires exactly { status, error, response, steps }. `meta` is an additive
// extra the GUI reads; it is ignored by any strict contract check.
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
