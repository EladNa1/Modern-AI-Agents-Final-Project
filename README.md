# CheckMate

**An autonomous agent that grades Calculus 1 exams.**

Give it a scanned exam. It returns a score, partial credit, and written feedback
for every question — reasoning through each solution the way a good TA does,
not following a rigid answer key.

Final project · *Modern AI Agents* course.

---

## The problem

Every exam round hits the same bottleneck:

- **Hours of manual work** — TAs grade hundreds of scripts, question by question.
- **Slow feedback** — students wait days to learn where they went wrong, long after they've moved on.
- **Inconsistent credit** — partial credit drifts between graders and across a long stack. Same mistake, different score.

Grading is a reasoning task, not a checklist. So instead of a rigid script, CheckMate
hands the job to an agent that reasons through a solution — and gives every student
instant, consistent, fair feedback on every question.

## What it does

1. **Input** — a full scanned exam (all pages).
2. **Process** — CheckMate reads every page, splits the exam into questions, and grades
   each one on the student's *actual method*, reasoning and verifying as it goes.
3. **Output** — a score, partial credit, and written feedback per question, totaled into the final grade.

### Example — one question, graded

> **Q3.** Compute `d/dx [ x² · ln x ]`
>
> **Student wrote:** `= 2x · ln x`  *(stops after the first product-rule term)*
>
> **CheckMate: 3 / 5**
> - Product rule applied to the first term — correct.
> - Missing the second term: `x² · (1/x) = x`.
> - Method sound, one term omitted → partial credit.
> - Symbolically verified: expected `2x·ln x + x`.

## How it works

A **Supervisor** agent runs a ReAct loop, grading question by question and stopping
when confident. For each question the order isn't fixed — it may re-read a blurry
scan, re-verify a step, skip reflection, or stop early, chosen from what it observes.
That is what makes it an agent, not a workflow.

Five roles from the course patterns, orchestrated in one loop:

| Role | Pattern | Job |
|------|---------|-----|
| **Supervisor** | ReAct | Tool-calling loop; picks the next tool and closes each question. |
| **Transcriber** | — | Reads the scan and splits it into questions. |
| **Retriever** | RAG | Pulls the official solution, rubric, and graded examples from the knowledge base. |
| **Verifier** | SymPy | Checks derivatives, integrals, and limits deterministically — no LLM, no guessing. |
| **Grader** | Few-shot | Scores the student's own method, guided by graded examples. |
| **Reflector** | Reflection | Critiques borderline grades; the Supervisor re-grades if needed. |

One model, many roles — every LLM call runs on **GPT-4o-mini**. The Supervisor is a
tool-calling loop, not a separate LLM; every call it makes becomes a step in the
`steps[]` trace.

**Interface:** scanned exam → `/api/execute` → response with `steps[]`.

## Why it works

- **Math checked by code** — derivatives, integrals, and limits are verified with
  SymPy, deterministically, not guessed by an LLM.
- **Knows when to defer** — genuinely ambiguous handwriting is flagged for human
  review, never silently guessed.
- **Cheap to run** — every call uses GPT-4o-mini; deterministic checks and tight
  per-question context keep a full exam within a ~$13 budget.

## Roadmap

Same agent, new scope: run it across a whole class, then reflect trends back to the lecturer.

- Class score distribution
- Hardest questions — which the class failed most
- Recurring mistakes — cluster the same error across students
- Topic mastery — strengths and gaps by topic, not just totals

## Repository contents

| Path | What it is |
|------|-----------|
| `CheckMate_DemoDay.pptx` | Demo Day pitch deck. |
| `Project Presentations - Demo Days.pdf` | Demo Days presentation brief. |
| `Data/` | Calculus 1 course material — lecture notes and past exams — used to build the knowledge base (solutions, rubrics, graded examples). |

> **Note:** `Data/` contains Technion course lecture notes and past exams, included
> here for reproducibility of the knowledge base. All rights to that material belong
> to their original authors.
