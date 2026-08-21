# CheckMate

**An autonomous Reflection Agent that grades Technion Calculus 1 (Hedva 1, 104041) exams.**

Give it a scanned exam. It reads every page, grades each question on the student's *actual
method* with partial credit and written feedback, critiques its own grade against the
official solution, and escalates to a human when unsure — grounded in the course's own
solutions and lecture notes, not a rigid answer key.

Final project · *Modern AI Agents* course · Elad Nachalieli · Shiri Haboob · Yaron Mozes.

---

## 1. The problem

Grading a proof-based Calculus course by hand doesn't scale, and it's a *reasoning* task,
not a checklist: in Hedva 1 the credit lives in the **justification structure** (monotonicity
established on each side, the quantifier present, the hypotheses stated) — not in a computable
final answer. CheckMate hands that judgement to an agent and gives every student fast,
consistent, grounded feedback per question, flagging for a human exactly the cases a TA
should see.

## 2. What it does

1. **Input** — a scanned exam (PDF or images, all pages).
2. **Process** — renders each page, reads it with vision OCR, auto-detects which exam it is,
   retrieves the matching official solution, grades each question on the student's own method
   (with self-consistency), critiques the grade, and accumulates a per-question gradebook.
3. **Output** — a score + partial credit + feedback per question, a deterministic final
   report (auto-graded subtotal vs. total-including-unreviewed, missing questions, a TA-review
   queue), and the full `steps[]` execution trace.

## 3. Architecture — a six-stage Reflection Agent

The design's answer to a capacity-limited grader is **self-critique + grounding**, not a
parallel deterministic checker. Every stage is a Python module; nothing bolts a new stage
onto the pipeline.

```
scan → Parser → Router → Retriever → Grader ⇄ Reflector → GradeBook → Result
        vision   scope    RAG        self-      critique/    assemble + completion
        OCR      to exam  (solution  consist.   approve/     (arithmetic, zero-LLM)
                          + notes)   median-N   escalate
```

(The reflection loop itself — grouping fragments, allocating passes, enforcing ceilings —
is control flow in `checkmate/orchestrator.py`, not a model stage: every LLM call it makes
is logged in `steps[]` under the module that made it.)

Every row below is a module name that appears verbatim in `steps[]`, in the architecture
diagram, and in `/api/agent_info` — the course grades that consistency.

| Stage | File | Job |
|-------|------|-----|
| **Config** | `checkmate/config.py` | Zero-LLM run-setup snapshot, logged as the FIRST step of every run: the active tuning knobs, the resolved model ids (chat / grader / embedding), and the vector backend — so any number in the trace is attributable to the configuration that produced it. |
| **Parser** | `checkmate/parser.py` | Vision OCR: transcribes each page faithfully, splits into questions, reads exam identity (course/date/מועד) off the cover, and re-reads faint regions (zoom, opt-in). A deterministic post-pass splits a merged True/False page into per-item `TF-n` fragments (zero LLM). Bounded by its own wall-clock deadline (`parse_deadline_seconds`): past it no NEW vision call is started, pages in flight finish, and skipped pages are reported in the trace rather than silently dropped. Does **not** grade. |
| **Zoom** | `checkmate/zoom.py` | Parser **Pass 2**, opt-in (`CHECKMATE_ZOOM=1`), logged under its own module name when it runs: re-reads a low-confidence fragment from a magnified crop of its own region. Off by default, so it does not appear in a default trace. |
| **Router** | `checkmate/orchestrator.py` + `checkmate/guard.py` + `kb/exams.py` | Two autonomous decisions. **Scope guard**: refuses out-of-domain requests (and non-exam scans that match nothing in the KB) politely, BEFORE spending grader tokens — the refusal is logged as a Router step. **Exam scoping**: manual override → auto-detected from the scan → unscoped fallback. |
| **Retriever** | `checkmate/retriever.py` | RAG **with a verification layer** — retrieval is checked, never trusted blindly: matches below a similarity floor (`MIN_SCORE`) are discarded so the agent grounds on *nothing* (and escalates) rather than on the wrong solution; retrieval is scoped to the exam identified from the scan; a strong content-overlap match outranks an unreliable id label; the Grader is instructed to reject a retrieved solution that doesn't match the question in front of it; and contested answer keys are checked symbolically offline with SymPy (the Q2b root-count key, settled by the check during a ground-truth dispute). Pinecone semantic search + exact-id and content-overlap fallbacks over bundled JSON, plus top-k course-notes chunks. |
| **Grader** | `checkmate/grader.py` | Scores the student's actual method with partial credit + feedback. **Self-consistency**: grades N times, takes the median, escalates on disagreement. At the run deadline the extra samples are dropped (the first always runs) and the grade is flagged `samples_cut_by_deadline` — never reported as a full N-sample vote it did not take. |
| **Reflector** | `checkmate/reflector.py` | Critiques the proposed grade **against the retrieved evidence** (official solution + transcript in context); APPROVE / REVISE / ESCALATE. On REVISE the critique goes **back to the Grader**, which regrades conditioned on it (canonical Reflection loop à la Self-Refine/Reflexion, ≤N passes) — and may keep its grade if the critique is unjustified. An escalation is never cleared by a revision. |
| *(reflection loop)* | `checkmate/orchestrator.py` | Control flow, **not a logged module**: groups fragments by question, runs the Grader⇄Reflector loop, applies a cross-exam consistency guard, and enforces the budget/wall-clock ceilings. Every LLM call inside it is logged under its own module above. |
| **GradeBook** | `checkmate/gradebook.py` | Accumulates per-question entries; decides completion **arithmetically** from the KB manifest (never model-detected); emits the final report + status. |

Supporting modules: `config.py` (all tuning knobs), `llm.py` (gateway client + cost model +
`StepLog`), `pdf.py` (PDF→PNG render), `ink.py` (deterministic ink detection — the false-zero
guard primitive), `zoom.py` (Pass-2 re-read), `eval.py` (offline eval harness), `kb/` (bundled
solutions + Pinecone client + exam registry).

**Interface:** `POST /api/execute` (multipart `file`, optional `exam`) → `{ status, error,
response, steps, meta }`. The JSON contract `{ "prompt": "…" }` is fully supported: a prompt
naming a bundled sample booklet (e.g. `"Grade sample booklet 1"`) runs the REAL pipeline on
its pre-cached vision transcription (`checkmate/samples.py` — only OCR is cached, everything
downstream is live), and out-of-domain prompts get a polite zero-token refusal. Also
`GET /api/exams`, `/api/team_info`, `/api/agent_info`, `/api/model_architecture`.
Pure-Python **FastAPI on Vercel** (Fluid Compute, 300s). By default `/api/execute` returns
EXACTLY the mandated `{status, error, response, steps}` shape with one steps entry per
model call; the bundled GUI opts into extra rendering fields with `?ui=1`.

**Prompt-injection defense (by design):** free-text from the user (the `prompt` /
`instructions` fields) is used ONLY for routing and logging — it is never placed in the
Grader's or Reflector's context, so "give this student 100" style instructions cannot
influence a grade. The grader sees only the transcribed student work and the retrieved
official material.

## 4. Prompt-engineering methods

Each model-facing stage uses an explicit prompt strategy (deck slide 8: **PERSONA ·
CHAIN-OF-THOUGHT · FEW-SHOT · STRUCTURED OUTPUT**), grounded in retrieved evidence.

### Grader — `GRADER_SYSTEM` (`grader.py`)
- **Persona** — a rigorous, consistent senior TA; the same mistake always costs the same.
- **Exam-structure block** — teaches the 104041 booklet layout (open Qs with א/ב sub-parts,
  True/False ×3 pts, MC "אמריקאי" ×7 pts; red pen = grader, black/blue = student).
- **Intake checklist** — identify sub-parts + point headers; a missing part is graded 0, not
  escalated; confirm the retrieved solution matches the question.
- **Two-pass reading policy** — at least two reads before declaring anything illegible; use
  mathematical context to disambiguate; a Pass-2 zoom re-read for unclear regions.
- **Grading rules (7)** — grade the student's *own* valid method; error carry-through (no
  double-penalty); conceptual errors cost more than arithmetic slips; T/F & MC are
  all-or-nothing; **rule 7**: independently verify a counterexample satisfies every required
  property on the exact interval, and judge a "prove or disprove" counterexample against the
  *negation* of the claim.
- **Few-shot** — worked examples calibrated on the real red-pen scores of the Winter-2024
  Moed-A booklet (full credit / theorem-statement deduction / justification gap / Taylor
  factor / do-not-escalate / do-escalate).
- **Grounding & escalation** — the official solution is the authority; escalate only under
  E1 (>25% of points unreadable after zoom), E2 (load-bearing out-of-syllabus step
  unverifiable), or E3 (official solution missing/contradictory).
- **Structured output** — a strict JSON object (`question_id, score, max, subscores, status,
  feedback, justification, confidence, read_attempts, flags, sources`).
- **Self-consistency** (code, not prompt) — N samples → median; sample spread beyond the
  disagreement threshold → escalate with a `grader_disagreement` flag.

### Parser — `PARSER_SYSTEM` (`parser.py`)
Transcribe-only persona (never grades); faithful transcription preserving Hebrew + math;
splits into questions with a per-fragment legibility **confidence**; `[illegible]` inline;
LaTeX for math; optional `exam_meta` (course/date/מועד) off the cover; structured JSON.

### Reflector — `REFLECTOR_SYSTEM` (`reflector.py`)
Receives the **proposed grade + official solution + student transcript** and critiques rather
than re-grades from memory; chooses exactly one action (APPROVE / REVISE / ESCALATE);
structured JSON. Skipped entirely on T/F + MC (no argument to critique).

## 5. Hyperparameters

**All tuning knobs live in one place — `checkmate/config.py` (`AgentConfig` / `CONFIG`)** —
and the active config is logged into every run trace and eval report (a number that can't be
attributed to a config is not a measurement). Env vars override the marked ones without a
redeploy.

### Grader (self-consistency) — `checkmate/config.py`
| Knob | Value | Meaning | Env override |
|------|-------|---------|--------------|
| `grader_samples` | **3** | N samples for open/proof questions | `CHECKMATE_GRADER_SAMPLES` |
| `grader_samples_high` | **5** | N for high-point open questions (≥ threshold) | `CHECKMATE_GRADER_SAMPLES_HIGH` |
| `grader_samples_tf_mc` | **3** | N for True/False + MC — median-of-3 (a single call proved unstable in eval) | `CHECKMATE_GRADER_SAMPLES_TFMC` |
| `high_point_threshold` | **15** | ≥ this ⇒ use `grader_samples_high` | — |
| `disagreement_frac` | **0.25** | escalate when sample spread > max(floor, frac·max) | — |
| `disagreement_floor` | **1.5** | floor (pts) for the disagreement threshold | — |

### Reflection — `checkmate/config.py`
| Knob | Value | Meaning | Env override |
|------|-------|---------|--------------|
| `max_revise_passes` | **2** | passes for high-stakes open questions | `CHECKMATE_MAX_REVISE_PASSES` |
| `reflection_passes_open` | **1** | passes for smaller open questions | — |
| `reflection_high_threshold` | **15** | ≥ this ⇒ full `max_revise_passes` | — |
| `reflect_tf_mc` | **False** | reflect on T/F + MC? (skipped) | — |
| `max_reflection_tokens_per_q` | **2200** | cumulative reflector token budget/question → early exit + `reflection_incomplete` | — |

### Parser / vision — `checkmate/config.py`, `pdf.py`, `parser.py`, `zoom.py`, `ink.py`
| Knob | Value | File | Meaning | Env override |
|------|-------|------|---------|--------------|
| `render_max_pages` | **24** | config.py | pages rendered per upload — covers the full 18-20 page booklets; parallel parsing keeps it fast | `CHECKMATE_MAX_PAGES` |
| `parser_max_tokens` | **2500** | config.py | parser output cap | — |
| `RENDER_DPI` | **150** | pdf.py | page render DPI | — |
| `CHECKMATE_ZOOM` | **off** | parser.py | opt-in Pass-2 zoom re-read | `CHECKMATE_ZOOM` |
| `ZOOM_CONF_THRESHOLD` | **0.55** | parser.py | fragment confidence below which zoom fires | — |
| `DEFAULT_SCALE` | **2.5** | zoom.py | zoom magnification | — |
| ink grid / dpi / stride | **(12,16) / 120 / 3** | ink.py | ink-density detector params | — |

### Grader/reflector token budgets — `checkmate/config.py`
| Knob | Value | Meaning |
|------|-------|---------|
| `grader_max_tokens` | **1800** | open questions (walk the argument step by step) |
| `grader_max_tokens_tf_mc` | **250** | T/F + MC (answer + one-line reason) |
| `reflector_max_tokens` | **900** | reflector critique |

**Input is never truncated** — the student transcript and official solution for the graded
question always go in full (truncation of either is logged as a defect, not a tuning outcome).

### Retriever — `checkmate/retriever.py`
| Knob | Value | Meaning |
|------|-------|---------|
| `MIN_SCORE` | **0.5** | min cosine similarity to trust a semantic solution match |
| `NOTES_MIN_SCORE` | **0.3** | min similarity for a course-notes chunk |
| solution top-k / notes k | **1 / 3** | exact solution match; supporting-notes breadth |

### Cost / budget — `checkmate/config.py`
| Knob | Value | Meaning |
|------|-------|---------|
| `price_input_per_1k` | **$0.00075** | ⚠ ESTIMATE — set the real gateway input rate |
| `price_output_per_1k` | **$0.00300** | ⚠ ESTIMATE — set the real gateway output rate |
| `price_embed_per_1k` | **$0.00002** | embedding tokens (Retriever queries) — cheap, but counted |
| `max_run_cost_usd` | **$0.75** | per-run ceiling; the run aborts before the next question if exceeded |

### Run ceilings (time) — `checkmate/config.py`, `checkmate/llm.py`
Vercel kills the whole request at **300s** (`vercel.json` `maxDuration`). Three nested
bounds keep a run inside it — a cost ceiling alone cannot, because it is only checked
*between* calls and cannot interrupt one already in flight.

| Knob | Value | File | Meaning | Env override |
|------|-------|------|---------|--------------|
| `LLM_TIMEOUT_SECONDS` | **45s** | llm.py | per-call HTTP timeout. Without it the OpenAI SDK defaults to a **600s** read timeout — twice the platform limit, so a single hung call returns nothing at all | `CHECKMATE_LLM_TIMEOUT` |
| `LLM_MAX_RETRIES` | **1** | llm.py | SDK retries per call (its default is 2, which triples the worst case) | `CHECKMATE_LLM_RETRIES` |
| `parse_deadline_seconds` | **120s** | config.py | the Parser's share: past it no NEW vision call starts, so a long upload cannot consume the run before a single question is graded | `CHECKMATE_PARSE_DEADLINE` |
| `max_run_seconds` | **240s** | config.py | run guard: checked before each question (absolute, plus a projection from the average so far) and inside the Grader's sample loop | `CHECKMATE_MAX_RUN_SECONDS` |

### Models — `checkmate/env.py` (env-driven)
| Knob | Value | Meaning |
|------|-------|---------|
| `LLMOD_MODEL` | `MB5R2CF-azure/gpt-5.4-mini` | parser + default chat model |
| `LLMOD_GRADER_MODEL` | *= `LLMOD_MODEL`* | grader + reflector model — one env var away from a stronger model **if the key allowed it** (it is currently restricted to the mini; stronger ids return HTTP 403) |
| `LLMOD_EMBED_MODEL` | `…/text-embedding-3-small` | embeddings (1536-dim) |
| **temperature** | **gateway default (~1.0)** | **not tunable** — gpt-5.4-mini rejects any explicit `temperature` (HTTP 400). The default varies across identical calls, which is what makes self-consistency informative; a "cold reflector" is not achievable on this model. |

## 6. Knowledge base + RAG

Two sources embedded into **Pinecone** (1536-dim, `text-embedding-3-small`):
1. **Official solutions** — one chunk per question/sub-part with metadata `{exam_id, question,
   part, max_points, verified}`, retrieved by exact metadata match (never pure semantic).
   Bundled JSON under `checkmate/kb/solutions/`.
2. **Course lecture notes** — chunked and embedded for grounding (definitions, theorem
   statements, allowed tools).

Because near-identical questions recur across exams ("state the IVT"), retrieval is **scoped
to the identified exam** — the disambiguating signal is the exam identity read off the scan,
not the student's answer. Keys are tagged `verified` vs `authored`: a key is marked
`verified` only after an offline SymPy check of its final answer — currently the once-
contested key, 2024W-A Q2b, verified to be exactly **0** real roots (`f ≥ 1 > 0`) when our
first reading of the red pen was in doubt. (A later scan re-audit showed the human's 7/10
there was a legitimate deduction for a derivative sign slip — the key and the human agree;
the SymPy check is what settled the dispute.) All other keys are `authored` from the
official solutions; proof-style questions have no machine-decidable key to verify.

## 7. Evaluation + budget model

`python -m checkmate.eval` — offline, zero LLM: routing accuracy + a corpus-wide deterministic
**ink pass** (the "region contains ink" side of the false-zero metric).
`python -m checkmate.eval --live` — grades booklets against human red-pen ground truth
(`eval/graded/*.json`) and reports open-question MAE, T/F+MC exact-match, **false-deduction
rate *and severity*** (points removed from flawless work), escalation **precision/recall**, and
**estimated cost by stage** — under the `max_run_cost_usd` ceiling.

**Budget rules (hard):** vision is the scarce call. Each booklet is parsed **once** and its
transcription cached to `eval/ocr_cache/` (keyed by a parser-source hash); re-runs where only
the grader/reflector/config changed reuse the cache for free. Cost is instrumented per stage
and printed; a run aborts rather than exceed the ceiling.

## 8. Running locally

```bash
pip install -r requirements.txt
cp .env.example .env.local          # fill in LLMOD_KEY (+ optional PINECONE_API_KEY)
python -m uvicorn api.index:app --reload --port 8000   # http://localhost:8000
```

Without keys the app still serves the full API contract via a deterministic mock, so every
endpoint works with no secrets. Tech: **Python · FastAPI + Jinja on Vercel** · OpenAI-compatible
LLMod.ai gateway · Pinecone · PyMuPDF (PDF render + ink detection). SymPy is an **offline-only**
KB-verification tool, never in the request path.

## 9. Known limitations

- **Grader reasoning ceiling** — the mini makes systematic math errors even with correct
  grounding; self-consistency catches *stochastic* disagreement but not *consistent* wrongness.
  A stronger grader model is the real lever, and it is blocked by the restricted key (§5).
- **Few-shot Example 3** in `GRADER_SYSTEM` teaches the SymPy-verified key (0 solutions)
  plus the rubric lesson from the graded booklet: a correct final count with a sign slip in
  the derivative line earns 7/10, not full marks — correct conclusions do not erase
  incorrect intermediate work.
- **Evaluation corpus is tiny** — two graded booklets of one paper (one ground-truth entry
  was initially disputed until a scan re-audit resolved it in the human's favor). Numbers
  are a regression tripwire, not a population estimate — `python scripts/regression_check.py`
  re-checks the bundled captures against the human ground truth; further tuning waits on
  more graded booklets (ideally a second distinct exam paper).

## 10. Repository layout

| Path | What it is |
|------|-----------|
| `api/index.py` | FastAPI app — the GUI + API routes (the Vercel entrypoint). |
| `checkmate/` | The agent: `parser`, `retriever`, `grader`, `reflector`, `orchestrator`, `gradebook`, `config`, `llm`, `pdf`, `ink`, `zoom`, `eval`. |
| `checkmate/kb/` | Knowledge base — bundled solution JSON, exam registry, Pinecone client. |
| `templates/` · `static/` | Jinja UI + CSS. |
| `eval/` | Ground-truth booklet scores + generated reports + OCR cache. |
| `scripts/` | Dev-only ingest / index / test harnesses (not deployed). |
| `Data/` | Course material + past/graded exams used to build the KB (rights belong to the original authors). |

> `Data/` contains Technion course material and past exams, included for reproducibility of
> the knowledge base. All rights to that material belong to their original authors.
