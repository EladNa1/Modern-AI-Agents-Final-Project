# PLAN — CheckMate real agent (progress tracker)

Replace the mock (`lib/mockAgent.ts`) with the real Reflection Agent from the deck
(`CheckMate.pptx` slide 7), conforming to the course brief (`Project.pdf`). Built **part by
part**; this file is updated after each phase. Full plan of record:
`C:/Users/eladn/.claude/plans/mutable-napping-seal.md`.

## Architecture (deck slide 7) — Reflection Agent
`Scanned exam → Parser (vision OCR) → Retriever (RAG) → Grader → Reflector (revise ≤N) → Approve | Escalate → Result`
Grounded on a Knowledge base (course notes · official solutions · TA-graded examples).
System prompt uses persona · chain-of-thought · few-shot · structured output (slide 8).

## Contract (brief)
- Endpoints: `GET /api/team_info`, `GET /api/agent_info`, `GET /api/model_architecture` (png), `POST /api/execute`.
- `/api/execute` → `{ status, error, response, steps }`; each step `{ module, prompt:{System_prompt,User_prompt}, response }`.
- Models: `MB5R2CF-azure/gpt-5.4-mini` (text+vision), `MB5R2CF-azure/text-embedding-3-small`.
- Provider LLMod.ai; DBs Supabase + Pinecone; budget **$13**; Vercel ≤300s/call; GUI at `/`, no auth; due 23/8/2026.

## Progress

- [x] **Phase 0 — Foundations & safety**: repo tracker, secrets→env (+rotate key), types aligned to brief casing, real team names, build passes.
- [x] **Phase 1 — LLM client + Parser (vision OCR)**: `lib/llm.ts`, `lib/agent/parser.ts`; transcribe a real scan → structured questions.
- [x] **Phase 2 — Grounding / Retriever (inline)**: ingest official solution → app-bundled JSON; `lib/agent/retriever.ts`.
- [x] **Phase 3 — Grader**: `lib/agent/grader.ts` (persona·CoT·few-shot·structured); grounded grade for one question.
- [x] **Phase 4 — Reflector + orchestrator**: `lib/agent/reflector.ts`, `lib/agent/orchestrator.ts`; wire `/api/execute` (mock stays as fallback).
- [x] **Phase 5 — Diagram & info consistency**: regenerate `architecture.png` (Parser·Retriever·Grader·Reflector), update `agent_info`, fix UI copy.
- [x] **Phase 6 — PDF support**: PDF→PNG page rendering feeding Parser.
- [x] **Phase 7 — Pinecone RAG upgrade**: embed KB (official solutions + full course notes), vector retrieval.
- [ ] **Phase 8 — Supabase persistence** *(optional)*: store runs + escalations.
- [ ] **Phase 9 — Verifier / SymPy** *(optional, slide 5)*: deterministic symbolic check.
- [ ] **Phase 10 — Deploy & final QA**: Vercel env, preview→prod, all endpoints verified, record URL.

## Open items (fill as we go)
- Teammate emails + `group_batch_order_number` for `team_info`.
- Pinecone/Supabase accounts exist? (Phases 7–8)
- Target exam for first real grade (default: a scan under `Data/…/סריקות חדווא 1/` with a matching `sol.pdf`).

## Changelog
- **Phase 0 done** (2026-07-19):
  - Created this tracker.
  - Secrets → env: `lib/env.ts`, `.env.local` (gitignored, has current key), `.env.example` (placeholders); `.gitignore` now ignores `.env*.local`. Stripped hardcoded key defaults from `send_slide.py` / `vision_test.py`. **Action for user: rotate the old key `sk-ifk…` — it is in git history.**
  - `lib/types.ts` is the single source of truth for `Step`/`QuestionResult`/`ExecuteResult`, now using brief casing `System_prompt`/`User_prompt`. `lib/mockAgent.ts` and `app/page.tsx` import it.
  - `npm i openai`. `team_info` now lists Elad Nahalieli, Shiri Haboob, Yaron Mozes (emails + group number still TODO).
  - `npm run build` passes; all four routes present.
- **Phase 1 done** (2026-07-19):
  - `lib/llm.ts`: OpenAI-compatible `chat()` (text+vision), lenient `extractJson()`, `StepLog` recorder (brief-shaped steps + token tally).
  - `lib/agent/parser.ts`: `runParser()` — vision OCR → `{questions:[{id,text,latex,confidence}]}`, logs a `Parser` / `Vision OCR` step.
  - Dev harness `scripts/test_parser.ts` (dev-only, `node --env-file=.env.local --import tsx scripts/test_parser.ts [img]`).
  - **Verified** on `public/exam_q3c_scan.png`: faithful Hebrew + LaTeX transcription, `[illegible]` on overwritten parts, confidence 0.58, captured the student's boxed **−2/3** (the slide-5 sign error; correct is +2/3). Cost ~1790 tokens/page — well within $13.
  - **Gotcha:** gpt-5.4-mini via the gateway only supports the **default temperature** (temperature=0 → 400). `chat()` omits temperature unless passed; Parser sends none.
  - Build passes with the new modules.
- **Phase 2 done** (2026-07-19):
  - Source exam auto-detected: **2023w final A (104041), Q3(c), 9 pts** — `lim_{x→0⁻} (∫_{x⁶}^0 sin√t dt)/x⁹`, official answer **+2/3**.
  - `scripts/ingest_solution.py` (dev-only): renders exam+solution pages via pymupdf, vision-OCRs to clean Hebrew+LaTeX (course PDFs store Hebrew in a legacy font → plain text is mojibake), writes `lib/kb/solutions/2023w_final_A.json` (bundled under `lib/`, so it ships to Vercel).
  - `lib/agent/retriever.ts`: `retrieve(id, text)` — id-normalized match (`Q3c`/`3c`/`3ג` all map), sin√t/x⁹ signature fallback, logs a `Retriever` / `RAG` step. Verified via `scripts/test_retriever.ts` (no LLM).
  - Ingest cost ~2735 tokens.
- **Phase 3 done** (2026-07-19):
  - `lib/agent/grader.ts`: `runGrader()` — slide-8 system prompt (persona · chain-of-thought · few-shot · structured JSON), grounded strictly in the retrieved solution, partial credit, escalates instead of guessing; logs a `Grader` / `Few-shot` step. Derives status from score + confidence.
  - Full-chain harness `scripts/test_grade.ts` (Parser→Retriever→Grader).
  - **Verified** on `exam_q3c_scan.png`: grade **5 / 9, partial** — "method correct, credit lost on the sign error (x<0 ⇒ √(x⁶)=−x³)", confidence 0.92. **Matches the deck's slide-5 illustrative grade exactly (+5 method / −4 sign).** Trace = Parser→Retriever→Grader. Full run ~4047 tokens.
- **Phase 4 done** (2026-07-19):
  - `lib/agent/reflector.ts`: `runReflector()` — critiques the grade vs. the official solution, returns APPROVE / REVISE / ESCALATE; logs a `Reflector` / `Reflection` step.
  - `lib/agent/orchestrator.ts`: `runAgent({images,instructions,sourceLabel})` — Parser once, then per question Retriever→Grader→Reflector (revise ≤2 passes), aggregated to a brief-shaped `ExecuteResult` (+`meta`). Uses the Retriever's canonical id for display. Mock fallback when no key or no image.
  - `app/api/execute/route.ts`: reads uploaded **image** bytes → data URL → `runAgent`; PDFs/Word and `{prompt}` JSON take the mock fallback (PDF is Phase 6). `maxDuration` = 300.
  - **Verified via the real HTTP endpoint**: `/api/execute` on the scan → 200 in ~8s, **Q3c 6/9**, steps `[Parser, Retriever, Grader, Reflector]`, top-level keys exactly `status,error,response,steps` (+meta), prompt keys `System_prompt/User_prompt`. `team_info` real; `model_architecture` returns `image/png`.
  - Note: gpt-5.4-mini forces temperature=1, so the score varies 5–6/9 across runs — both fair partial credit for the sign error.
- **Phase 5 done** (2026-07-19):
  - `scripts/make_architecture.py` rewritten to the Reflection-Agent layout: main row `Scanned exam → Parser → Grader → Reflector → Result`, with `Knowledge base → Retriever` feeding Grader+Reflector (dashed RAG evidence), amber `revise ≤ N` loop and `Escalate → teacher`. Module names locked to the `steps[]` trace. Regenerated `public/architecture.png` (deterministic; re-run reproduces byte-identical 80735 B).
  - `agent_info` description now names the four logged modules (Parser · Retriever · Grader · Reflector) and states what CheckMate cannot do; ties copy to the diagram.
  - UI copy (`app/page.tsx`) aligned: trace section states names match the diagram; accepts image now, PDF/Word flagged as sample fallback.
  - Consistency grep: no stale module names (Verifier/Solver/Extractor/…) anywhere. `npm run build` passes, all four routes present.
- **Phase 6 done** (2026-07-19):
  - Chosen engine: **mupdf 1.28 (WASM)** — no native binary, Vercel-safe, same MuPDF core as the pymupdf ingest script. `npm i mupdf`; `next.config.js` → `serverExternalPackages:["mupdf"]`.
  - `lib/agent/pdf.ts`: `renderPdfToImages(Uint8Array)` → per-page PNG data URLs at 150 DPI (`Matrix.scale`, `DeviceRGB`, `asPNG`), cap `MAX_PAGES=12`, returns `{images,pageCount,rendered}`. Dynamic-imported so image uploads never load the WASM.
  - **Parser now reads page by page** (per user's ask): `runParser` loops one vision call per page, logs one `Parser`/`Vision OCR` step per page, and merges questions across pages by normalized id (`3ג`/`Q3c`/`3 c` collapse; continuations concatenate, min-confidence). Single-image path is byte-identical to before.
  - `app/api/execute/route.ts`: `.pdf` → render → feed pages to `runAgent`; clean 400 on corrupt/empty/encrypted PDF; sourceLabel notes page count (and N/M when capped). `.doc/.docx` still take the mock fallback. UI hint updated (PDF+image supported; Word = sample).
  - Dev harness `scripts/test_pdf.ts` (no LLM). **Verified**: `2023w final A.pdf` (177 KB) → 10 pages, all valid PNGs, Hebrew+math crisp at 150 DPI. **No-regression**: `test_grade.ts` on the scan still `Q3c 6/9 partial`, trace Parser→Retriever→Grader, 4116 tokens. `npm run build` passes.
  - Remaining: an end-to-end LLM grade of a real multi-page **student** PDF is untested (only blank/solution PDFs on disk) — render + wiring + single-page grade are all verified.
- **Phase 7 done** (2026-07-19):
  - **Pinecone vector RAG** (`@pinecone-database/pinecone` v8, index `checkmate-kb`, dim 1536 cosine serverless, auto-created). Key in `.env.local` (gitignored) + `.env.example`; `lib/env.ts` `hasPinecone` gates it (falls back to bundled exact-match when unset). `lib/kb/pinecone.ts` = client + `ensureIndex` + typed metadata. `embed()` added to `lib/llm.ts` (gateway `text-embedding-3-small`, verified 1536-dim).
  - **Two record kinds** in one index (`kind` metadata filter): `solution` (official exam-question solutions) + `notes` (course lecture chunks).
  - **KB expanded**: generalized `scripts/ingest_exam.ts` (render→per-page structured vision extraction→merge) ingested **2024w & 2025w final A** (23 + 10 questions) alongside curated 2023w Q3c → **34 solution vectors**. `scripts/index_kb.ts` embeds+upserts (auto-creates index).
  - **Full course material in RAG** (per user): `scripts/ingest_notes.ts` — the 116-page lectures PDF has clean Unicode text (no mojibake), so plain mupdf `structuredText` extraction (no vision OCR, embed-only) → **116 note vectors**. Total **150 vectors**.
  - **Retriever reworked** (now async): semantic Pinecone query is PRIMARY (content-based → disambiguates the "Q3c" that now exists in 3 exams), filtered `kind:solution`, with exact-id + signature as offline fallback. New `retrieveNotes()` pulls top-k `kind:notes` chunks; the Grader prompt gains a non-authoritative "COURSE MATERIAL" block. Orchestrator logs two Retriever steps (solution + notes) per question.
  - **Reliability fix**: gpt-5.4-mini at temp=1 sometimes emitted unparseable JSON (0 questions). Added `jsonMode` (`response_format:json_object`, gateway-supported) to Parser/Grader/Reflector → 3/3 reliable.
  - **Verified**: filtered solution match (Q3c, semantic 0.50 / exact-id), notes hits on the limits chapter (p.26/30), reject on unrelated (Q99). Full `runAgent` E2E on the scan → **Q3c 5/9 partial** (sign error, matches deck), trace `Parser→Retriever(sol)→Retriever(notes)→Grader→Reflector×2`. `npm run build` passes.
  - Notes on quality: exam auto-extraction is good on open questions (e.g. Q7b Lagrange proof), coarser on multiple-choice; note text is RTL-reordered but topically retrievable (lower `NOTES_MIN_SCORE=0.3`).
- **UI redesign + README + GitHub push** (2026-07-19):
  - GUI (`app/page.tsx` + `globals.css`) rebuilt deck-consistent (palette already matched slides): added problem strip (slide 2), how-it-works (architecture + numbered Parser/Retriever/Grader/Reflector pipeline), why-it-holds-up (slide 9), knowledge-base (slide 6), team footer (slide 1). More explanations throughout.
  - **Architecture diagram now 1-to-1 with deck slide 7** (per user): `public/architecture.png` is an export of `CheckMate.pptx` slide 7 (PowerPoint COM, 1440×810). `scripts/make_architecture.py` (matplotlib, different layout) removed, replaced by `scripts/export_architecture.ps1` — deck is the single source of truth.
  - README rewritten to match the implemented system (Reflection Agent, gpt-5.4-mini, Pinecone RAG) — was stale (Supervisor/ReAct/SymPy/GPT-4o-mini).
  - Committed the whole real-agent implementation (curated: excluded stray images, `Data/DATA.pptx`, and this internal PLAN) and pushed to GitHub `main` (`39b8fa4` then `83e4f48`), rebased over teammate's `Data/` push (which added real **student exam scans** — usable for the pending multi-page E2E test).
- **Phase 6 follow-up — multi-page student-PDF E2E: DONE, and it exposed real defects** (2026-07-20):
  - `scripts/test_agent.ts` now accepts a **PDF** (same `renderPdfToImages` path as `/api/execute`) and prints elapsed time. `lib/agent/pdf.ts` `MAX_PAGES` is now env-tunable (`CHECKMATE_MAX_PAGES`, default 12) — real booklets are 18–23 pages, so the default silently dropped half of every exam.
  - **Run A — `Data/מבחן1/104041_2024_Winter_A_93.pdf`** (12/18 pages): pipeline ran clean (45 steps, exact brief schema, `System_prompt`/`User_prompt`) but nearly everything **escalated**. Root cause: **the KB does not contain this exam.** `Data/מבחני עבר חדווא 1/2024w/` and `2025w/` actually hold **104042 (Calculus 2)** papers, so the Phase-7 ingest produced `2024w_final_A.json` / `2025w_final_A.json` with `course: 104042` while the student scans in `Data/מבחן1|3/` are **104041 (Calculus 1)**. Agent escalated rather than fabricating a grade — correct behaviour, no grounding available.
  - **Run B — `Data/סריקות חדווא 1/104042_2025_Winter_A_100.pdf`** (23/23 pages, exam **is** in the KB, TA grade **100**): `status: ok`, **152.5s**, 23 Parser steps, 11 graded questions, **47/60**.
    - **Good:** the four properly-grounded open questions graded sensibly with genuinely useful feedback — Q3c 10/10, Q6a 9/10, Q10b 5/10, Q7 23/30.
    - **Defect 1 (blocker) — `points: 0` in the KB.** ~17 of 24 ingested questions (all the multiple-choice blocks 1a–1e, 2a–2d, 3a–3b, 5a–5d, 6a–6c) have `points: 0`, so `max` sums to **60 instead of 100** and those questions produce meaningless `0/0` grades. `scripts/ingest_exam.ts` never extracted per-question point values for MC sections.
    - **Defect 2 — duplicate / junk question ids.** Output contains `Q4` twice, `Q5` twice, and a pseudo-question `Page 2`. The same question is graded twice with **contradictory** verdicts (one `Q5` escalate, one `Q5` ok). The Parser's cross-page merge key does not collapse them.
    - **Defect 3 — harshness / split work.** TA gave this paper 100; the agent deducted on Q7 and Q10b partly because the student's continuation pages are not attached to the question they belong to (merge is page-local).
    - **Defect 4 — Vercel budget.** 23 pages took 152.5s; the 300s ceiling is close, and the default 12-page cap truncates a real booklet.
- **KB points extraction fixed — Defect 1 closed** (2026-07-20):
  - **New `lib/kb/examStructure.ts`** — a deterministic structure pass. Question ids and point values now come from the PDF's own **text layer**, never from the vision model, because a wrong point value silently corrupts every grade computed against it. Returns the skeleton (`Q6a=10 @page 5`), the exam total, and an `idsByPage` map.
    - Handles the visual (RTL-reordered) text Technion PDFs emit, including point values whose parentheses are split by intervening text (`נקודות( הוכיחו כי10)`).
    - Distinguishes **multiple-choice options from real sub-parts deterministically**: both print as a Hebrew letter, but only a real sub-part carries its own point value. This is what had been inflating the KB with fake questions.
    - **Legacy-Hebrew decode**: pre-2025 exam PDFs embed Hebrew in a cp1255 font that extracts as Latin-1 gibberish (`ä÷éèîúîì`). Fixed offset + reversing each Hebrew run restores it, applied per document only when no real Hebrew is present.
  - **`scripts/ingest_exam.ts` is now skeleton-driven**: the structure pass runs first, the id list plus a per-page hint ("this page shows Q6c") go into each vision call, points are copied from the skeleton, and anything the model invents is dropped as off-skeleton. Prints `TOTAL POINTS` and warns on any question that is 0-point or missing — a bad ingest is now loud instead of silent.
  - **Result on 2025w final A: `TOTAL POINTS: 100 ✓`**, 11 questions with exactly the right ids and weights (`Q1–Q5=8, Q6a/b/c=10, Q7a/b/c=10`) — replacing 24 entries of which ~17 were 0-point fakes. 2024w re-ingested to 7 correct Part-B entries (56 pts; its Part-A weights use different phrasing — `שכל אחת 7 נקודות` — so the total check correctly flags it as incomplete).
  - **Stale-vector handling**: this Pinecone index **rejects delete-by-id** (fails with a bare `Invalid request`, even for a single id), so a re-ingest that renames questions leaves the old vectors live. `scripts/index_kb.ts` now stamps every solution vector with a **generation** and writes `lib/kb/generation.json`; `retriever.ts` filters `gen` so superseded vectors become unreachable. 19 live vectors, 24 superseded and filtered out.
  - `lib/agent/pdf.ts` `MAX_PAGES` is env-tunable (`CHECKMATE_MAX_PAGES`, default 12) — real booklets run 18–23 pages. `scripts/test_agent.ts` now accepts a PDF and reports elapsed time. `npm run build` passes, all four routes present.
- **E2E re-measured after the fix — Defect 2 is now the blocker** (2026-07-20):
  - `104042_2025_Winter_A_100.pdf` (TA grade **100**), 23 pages, 144.5s: point values are now correct (`max` 86 vs. the earlier bogus 60) and the well-matched questions grade well — **Q6c 10/10, Q7a 8/10**.
  - But the total came out **23/86**, because the **Parser mis-assigns student pages to questions**: the output still contains `Q4`, `Q2`, `Q5` **twice each** plus a junk `Page2`, and most feedback is a variant of *"the student work does not match the official question"*. Work that is graded against the wrong official solution scores 0.
  - **Root cause (mirror of the KB bug, student side):** the Parser reads each scanned page independently, *guesses* which question the page belongs to, and the orchestrator merges by that guessed id. The KB side was fixed with a text-layer structure pass; a handwritten scan has no text layer, so the fix has to be different — **group by the Retriever's matched question instead of the Parser's guessed id**: parse pages → retrieve per page by content → group pages that matched the same official question → grade each group once. Not yet implemented.
- **Defect 2 closed — grouping now driven by retrieval, not by the Parser's guess** (2026-07-20):
  - `lib/agent/parser.ts`: `runParser` returns **per-page `ParsedFragment`s** and no longer merges across pages. Merging on the Parser's own reading of the question label was the bug: a handwritten answer is often unlabelled or mislabelled, so one answer split across two ids while unrelated work collapsed under a shared wrong one. `ParsedQuestion` → `ParsedFragment` (+`page`), updated in `grader.ts`, `reflector.ts`, and the dev harnesses.
  - `lib/agent/orchestrator.ts`: new `groupByRetrievedQuestion()` — the Retriever matches each fragment on **content**, and that match defines the question. Every page of one answer is stitched back together by `mergeFragments()` and graded exactly once. Work the KB cannot place goes into a single `Unmatched work` escalation instead of a scatter of invented one-line "questions".
  - **Verified** on `104042_2025_Winter_A_100.pdf` (23 pages, 134.6s): **no duplicate ids** — Q1, Q2, Q3, Q4, Q5, Q6c, Q7a, Q7c each appear exactly once (previously Q2/Q4/Q5 each appeared twice plus a junk `Page2`). Q5 went 0 → **8/8**. `npm run build` and `tsc --noEmit` clean.
- **Next blocker — Defect 5: retrieval is not scoped to the exam being graded** (2026-07-20):
  - Same run scored **29/79** against a TA grade of 100, and the reason is now visible in the output: it graded a **`Q8b`**, which does not exist in the 2025w exam — that id is from **2024w**. The KB holds three exams and nothing constrains a student's page to the paper they actually sat, so the Taylor question matched the wrong exam's version ("the official task is about approximating √11 near 9, while the student works with √16 and √20") and scored 0 on a correct answer.
  - Knock-on: Q6a, Q6b and Q7b never appear at all — their work was absorbed by wrong-exam matches.
  - **Proposed fix:** identify the exam once (majority vote over all fragment matches, or read it off the paper), then re-run retrieval with a `filter: { exam }` so every question is grounded in the paper the student actually sat.
  - Also still open: multiple-choice questions score 0 because the student's marked selection lives on a separate answer-grid page that is never associated with the question ("the transcription only shows the list of statements and no actual selection").
- **Phase 10 — deploy: BLOCKED on user** (2026-07-19): Vercel CLI installed (54.6.1) but not logged in (interactive). Repo on GitHub (public: `EladNa1/Modern-AI-Agents-Final-Project`). **User actions:** (1) **rotate the leaked LLMOD key** — it's public in git history AND anyone can drain the $13 budget; (2) run `vercel login`. Then set env (LLMOD_* + PINECONE_* + `checkmate-kb`) and `vercel --prod`. `team_info` TODOs (emails, group number) left as placeholders per user.
