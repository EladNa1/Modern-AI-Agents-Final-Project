# CheckMate

**An autonomous agent that grades Calculus 1 (Hedva 1) exams.**

Give it a scanned exam. It reads every page, grades each question on the student's
*actual method* with partial credit and written feedback, and returns the full
execution trace — reasoning through each solution the way a good TA does, grounded
in the official solution and the course notes, not a rigid answer key.

Final project · *Modern AI Agents* course · Elad Nachalieli · Shiri Haboob · Yaron Mozes.

---

## The problem

Grading Calculus by hand doesn't scale. Every exam round hits the same bottleneck:

- **Slow feedback** — students wait days to learn where they went wrong, long after they've moved on.
- **Inconsistent credit** — partial credit drifts between graders and across a long stack: same mistake, different score.
- **Missing feedback** — often just a number lands, with no explanation of what went wrong or how to improve.

Grading is a reasoning task, not a checklist. CheckMate hands the job to an agent that
reasons through a solution and gives every student **fast, consistent, fair** feedback
on every question.

## What it does

1. **Input** — a scanned exam (PDF or image; all pages).
2. **Process** — reads every page with vision OCR, splits the exam into questions, and grades
   each on the student's own method, grounded in the retrieved official solution and course notes.
3. **Output** — a score, partial credit, and written feedback per question, totaled into the final grade — plus the `steps[]` trace of every call the agent made.

### Example — one question, graded

The 2023w final, Q3(c) (9 pts): a limit `lim_{x→0⁻} (∫_{x⁶}^0 sin√t dt) / x⁹`.
The student's method is right but they lose a sign at `√(x⁶) = |x|³ = −x³` for `x < 0`,
reaching `−2/3` instead of the correct `+2/3`.

> **CheckMate: 5 / 9 — partial**
> - Method correct: bound flip, L'Hôpital via FTC, `sin θ/θ → 1`. **(+5)**
> - Sign lost at `√(x⁶) = |x|³ = −x³`; answer `−2/3`, correct is `+2/3`. **(−4)**

## How it works — a Reflection Agent

CheckMate grades each question first, then **critiques its own grade** against the
retrieved course evidence and revises until confident — approving, or escalating to a
human when unsure. Every LLM call runs on **gpt-5.4-mini** through the LLMod.ai gateway,
and every module logs a step in `steps[]`.

```
Scanned exam → Parser → Retriever → Grader → Reflector → Approve | Escalate → Result
                          (RAG over the knowledge base: solutions + course notes)
```

| Module | Pattern | Job |
|--------|---------|-----|
| **Parser** | Vision OCR | Reads each page, transcribes the student's work faithfully, splits it into questions. |
| **Retriever** | RAG | Pinecone vector search: pulls the matching official solution *and* the most relevant course-notes chunks. |
| **Grader** | Persona · CoT · Few-shot · Structured output | Scores the student's actual method with partial credit and feedback. |
| **Reflector** | Reflection | Critiques the grade against the evidence, revises up to N passes, then approves or escalates. |

**Interface:** `POST /api/execute` (multipart file, or `{ "prompt": … }` JSON) →
`{ status, error, response, steps }`, each step `{ module, prompt: { System_prompt, User_prompt }, response }`.
Also `GET /api/team_info`, `GET /api/agent_info`, `GET /api/model_architecture` (PNG).

## Why the grades hold up

- **Grounded in the source** — every grade is tied to the official solution and the course
  lecture notes, retrieved via RAG — never invented, and no rubric is fabricated.
- **Checks its own grade** — the Reflector critiques and revises until confident, capped at
  N passes so it always terminates.
- **Escalates when unsure** — low-confidence grades and unclear handwriting go to the teacher
  for review, never silently guessed.
- **Cheap to run** — every call uses gpt-5.4-mini; tight per-question context keeps a full exam within a ~$13 budget.

## The knowledge base

Two sources, embedded into **Pinecone** (1536-dim, `text-embedding-3-small`):

1. **Official solutions** — per-question solutions from past finals, with point values and
   grading notes: the ground truth each grade is measured against.
2. **Course material** — the full Calculus 1 lecture notes, chunked and embedded, so every
   grade is grounded in the course's own source of truth.

Dev tooling (`scripts/`, excluded from deploy): `ingest_exam.ts` (solution PDF → KB JSON),
`ingest_notes.ts` (lecture notes → Pinecone), `index_kb.ts` (embed + upsert solutions).

## Running locally

```bash
npm install
cp .env.example .env.local   # fill in LLMOD_KEY and PINECONE_API_KEY
npm run dev                  # http://localhost:3000
```

Without keys the app still serves the required API contract via a deterministic mock,
so every endpoint works with no secrets configured.

Tech: Next.js (App Router) · TypeScript · OpenAI-compatible LLMod.ai gateway · Pinecone ·
mupdf (WASM) for PDF page rendering. Deploys on Vercel (`maxDuration` 300s).

## Roadmap

Same agent, new scope — plus deeper verification:

- **Symbolic verifier (SymPy)** — cross-check derivatives, integrals, and limits deterministically.
- **Persistence** — store runs and escalations (Supabase) for teacher review.
- **Class analytics** — score distribution, hardest questions, recurring mistakes, topic mastery.

## Repository contents

| Path | What it is |
|------|-----------|
| `app/` | Next.js app — the GUI (`page.tsx`) and the four API routes. |
| `lib/agent/` | The agent modules: `parser`, `retriever`, `grader`, `reflector`, `orchestrator`, `pdf`. |
| `lib/kb/` | Knowledge base — bundled solution JSON + the Pinecone client. |
| `scripts/` | Dev-only ingest / index / test harnesses (not deployed). |
| `CheckMate.pptx` | Project pitch deck. |
| `Data/` | Calculus 1 course material — lecture notes and past exams — used to build the knowledge base. |

> **Note:** `Data/` contains Technion course lecture notes and past exams, included here for
> reproducibility of the knowledge base. All rights to that material belong to their original authors.
