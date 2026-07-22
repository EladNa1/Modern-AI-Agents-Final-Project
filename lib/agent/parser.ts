// Parser module (deck slide 7) — the vision stage of CheckMate.
// Reads the scanned exam, transcribes the student's handwritten work faithfully,
// and splits it into questions. It does NOT grade. Prompt adapted from send_slide.py.
import { chat, extractJson, StepLog, type ImageInput, type Usage } from "../llm";

// One block of student work found on one page. The Parser's `id` is only its reading
// of whatever label the student wrote — handwriting is often unlabelled or mislabelled,
// so it is a hint, not an identity. The Retriever decides which exam question a
// fragment actually answers (see the orchestrator), which is what fragments get
// grouped by.
export type ParsedFragment = {
  id: string; // the label as written, or the Parser's best guess
  text: string; // faithful transcription of the student's work (Hebrew + math preserved)
  latex?: string; // math re-expressed in LaTeX where helpful
  confidence: number; // 0..1 legibility/transcription confidence
  page: number; // 1-based page the fragment was read from
};

export type ParseResult = {
  fragments: ParsedFragment[];
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
  // One vision call PER PAGE (not all pages at once): more robust on long exams, and
  // each page is logged as its own Parser step in the trace. Fragments are returned
  // per page and deliberately NOT merged here — merging on the Parser's own guess of
  // the question label is what previously split one answer across two ids and grouped
  // unrelated work under a shared wrong one. The orchestrator groups them by the
  // question the Retriever matches instead.
  const total = { prompt: 0, completion: 0, total: 0 } as Usage;
  const raws: string[] = [];
  const fragments: ParsedFragment[] = [];

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

    const pageQs = normalizeFragments(
      extractJson<{ questions?: ParsedFragment[] }>(text)?.questions,
      p + 1
    );
    fragments.push(...pageQs);
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

  return { fragments, raw: raws.join("\n\n"), usage: total };
}

function normalizeFragments(
  qs: ParsedFragment[] | undefined,
  page: number
): ParsedFragment[] {
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
      page,
    }));
}
