// LLMod.ai gateway client (OpenAI-compatible) + a step recorder for the trace.
// Server-only: pulls the key from lib/env. Never import from a client component.
import OpenAI from "openai";
import { LLMOD_BASE_URL, LLMOD_KEY, LLMOD_MODEL, LLMOD_EMBED_MODEL } from "./env";
import type { Step } from "./types";

let _client: OpenAI | null = null;
function client(): OpenAI {
  if (!_client) _client = new OpenAI({ baseURL: LLMOD_BASE_URL, apiKey: LLMOD_KEY });
  return _client;
}

export type Usage = { prompt: number; completion: number; total: number };
export type ImageInput = { dataUrl: string; detail?: "low" | "high" | "auto" };

export type ChatArgs = {
  system: string;
  user: string;
  images?: ImageInput[];
  maxTokens?: number;
  temperature?: number;
  jsonMode?: boolean; // force response_format json_object — guarantees parseable JSON
};

export type ChatResult = { text: string; usage: Usage };

// One chat completion (optionally multimodal). Images ride in the user turn as
// data-URI image_url parts — the same shape send_slide.py uses.
export async function chat(args: ChatArgs): Promise<ChatResult> {
  const parts: OpenAI.Chat.Completions.ChatCompletionContentPart[] = [
    { type: "text", text: args.user },
  ];
  for (const img of args.images ?? []) {
    parts.push({
      type: "image_url",
      image_url: { url: img.dataUrl, detail: img.detail ?? "high" },
    });
  }

  const resp = await client().chat.completions.create({
    model: LLMOD_MODEL,
    max_tokens: args.maxTokens ?? 1500,
    ...(args.temperature != null ? { temperature: args.temperature } : {}),
    ...(args.jsonMode ? { response_format: { type: "json_object" } } : {}),
    messages: [
      { role: "system", content: args.system },
      { role: "user", content: parts },
    ],
  });

  const u = resp.usage;
  return {
    text: resp.choices[0]?.message?.content ?? "",
    usage: {
      prompt: u?.prompt_tokens ?? 0,
      completion: u?.completion_tokens ?? 0,
      total: u?.total_tokens ?? 0,
    },
  };
}

// Text embeddings via the same OpenAI-compatible gateway (text-embedding-3-small,
// 1536 dims). Used by the Retriever (Pinecone RAG) and the KB indexer. Accepts a
// single string or a batch; returns one vector per input, in order.
export async function embed(input: string | string[]): Promise<number[][]> {
  const batch = Array.isArray(input) ? input : [input];
  const resp = await client().embeddings.create({
    model: LLMOD_EMBED_MODEL,
    input: batch,
  });
  // The API may return items out of order — sort by index to be safe.
  return [...resp.data].sort((a, b) => a.index - b.index).map((d) => d.embedding as number[]);
}

// Lenient JSON extraction — models sometimes wrap JSON in prose or ```json fences.
// Returns the first balanced {...} object parsed, or null.
export function extractJson<T = unknown>(text: string): T | null {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1] : text;
  const start = candidate.indexOf("{");
  if (start === -1) return null;
  let depth = 0;
  for (let i = start; i < candidate.length; i++) {
    const c = candidate[i];
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(candidate.slice(start, i + 1)) as T;
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

// Accumulates brief-shaped Steps { module, prompt:{System_prompt,User_prompt}, response }
// for the /api/execute trace. One instance per run.
export class StepLog {
  readonly steps: Step[] = [];
  usage: Usage = { prompt: 0, completion: 0, total: 0 };

  add(
    module: string,
    system: string,
    user: string,
    response: unknown,
    pattern?: string,
    usage?: Usage
  ): void {
    this.steps.push({
      module,
      pattern,
      prompt: { System_prompt: system, User_prompt: user },
      response,
    });
    if (usage) {
      this.usage.prompt += usage.prompt;
      this.usage.completion += usage.completion;
      this.usage.total += usage.total;
    }
  }
}
