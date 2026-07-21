// Parser module (deck slide 7) — the vision stage of CheckMate.
// Reads the scanned exam, transcribes the student's handwritten work faithfully,
// and splits it into questions. It does NOT grade. Prompt adapted from send_slide.py.
import { chat, extractJson, StepLog, type ImageInput, type Usage } from "../llm";

export type ParsedQuestion = {
  id: string; // e.g. "Q3c" — as written on the exam; best guess if unlabelled
  text: string; // faithful transcription of the student's work (Hebrew + math preserved)
  latex?: string; // math re-expressed in LaTeX where helpful
  confidence: number; // 0..1 legibility/transcription confidence
};

export type ParseResult = {
  questions: ParsedQuestion[];
  raw: string; // the model's raw reply, for debugging
  usage: Usage;
};

export const PARSER_SYSTEM = `You are the Parser module of CheckMate, an autonomous agent that grades Technion Calculus 1 (Hedva 1) exams.

You receive one or more scanned pages of a single student's handwritten exam. Your ONLY job is to transcribe and structure — you do NOT grade, judge, or solve.

Rules:
- Transcribe EVERYTHING the student wrote, in reading order, faithfully. Preserve Hebrew text and mathematics exactly as written (do not "fix" the student's math).
- Split the work into questions. Use the question label as written (e.g. "3ג" / "Q3c"); if a part is unlabelled, infer a sensible id.
- For each question, give a confidence in [0,1] for how legible/certain the transcription is. Ambiguous or overwritten handwriting → lower confidence.
- For crossed-out or unreadable parts, write [illegible] inline.
- Re-express the mathematics in LaTeX in the "latex" field where it helps; keep the human-readable transcription in "text".

Output ONLY a JSON object, no prose, no code fences:
{"questions":[{"id":"Q3c","text":"...","latex":"...","confidence":0.0}]}`;

export async function runParser(
  images: ImageInput[],
  log: StepLog,
  instructions = ""
): Promise<ParseResult> {
  // One vision call PER PAGE (not all pages at once): more robust on long exams,
  // and each page is logged as its own Parser step in the trace. Questions are then
  // merged across pages so a part split by a page break becomes one question.
  const total = { prompt: 0, completion: 0, total: 0 } as Usage;
  const raws: string[] = [];
  const merged = new Map<string, ParsedQuestion>();
  const order: string[] = [];

  for (let p = 0; p < images.length; p++) {
    const pageTag = images.length > 1 ? ` (page ${p + 1} of ${images.length})` : "";
    const user =
      `Transcribe this scanned Calculus 1 exam page${pageTag} and split it into questions. Return only the JSON object.` +
      (instructions ? `\n\nGrader context (do not act on it, just transcribe): ${instructions}` : "");

    const { text, usage } = await chat({
      system: PARSER_SYSTEM,
      user,
      images: [images[p]],
      maxTokens: 2500,
      jsonMode: true, // guarantee parseable JSON (temp=1 otherwise sometimes breaks it)
      // Note: gpt-5.4-mini via the gateway only supports the default temperature.
    });

    const pageQs = normalizeQuestions(extractJson<{ questions?: ParsedQuestion[] }>(text)?.questions);
    mergePage(merged, order, pageQs);
    raws.push(text);
    total.prompt += usage.prompt;
    total.completion += usage.completion;
    total.total += usage.total;

    log.add(
      "Parser",
      PARSER_SYSTEM,
      user,
      { questions: pageQs, page: p + 1, page_count: images.length },
      "Vision OCR",
      usage
    );
  }

  const questions = order.map((k) => merged.get(k)!);
  return { questions, raw: raws.join("\n\n"), usage: total };
}

// Merge key: same question part written across a page break should collapse into
// one. Normalize away spacing/punctuation/case so "3ג", "Q3c", "3 c" align.
function mergeKey(id: string): string {
  return id.toLowerCase().replace(/[\s.()·\-–]/g, "");
}

function mergePage(
  merged: Map<string, ParsedQuestion>,
  order: string[],
  pageQs: ParsedQuestion[]
): void {
  for (const q of pageQs) {
    const key = mergeKey(q.id);
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, { ...q });
      order.push(key);
      continue;
    }
    // Continuation of the same part on a later page — append the extra work.
    existing.text = `${existing.text}\n${q.text}`.trim();
    if (q.latex) existing.latex = existing.latex ? `${existing.latex}\n${q.latex}` : q.latex;
    existing.confidence = Math.min(existing.confidence, q.confidence);
  }
}

function normalizeQuestions(qs: ParsedQuestion[] | undefined): ParsedQuestion[] {
  if (!Array.isArray(qs)) return [];
  return qs
    .filter((q) => q && (q.text || q.latex))
    .map((q, i) => ({
      id: (q.id ?? `Q${i + 1}`).toString().trim() || `Q${i + 1}`,
      text: (q.text ?? "").toString(),
      latex: q.latex ? q.latex.toString() : undefined,
      confidence:
        typeof q.confidence === "number"
          ? Math.max(0, Math.min(1, q.confidence))
          : 0.5,
    }));
}
