# PLAN — Ingest new exams (מבחן4, מבחן5) + grade-without-solution test

Date: 2026-07-22

## Scope
New exams pulled from origin: `Data/מבחן4`, `Data/מבחן5`, `Data/מבחן6`.

| Exam | Course | Term | Moed | Slug | Action |
|------|--------|------|------|------|--------|
| מבחן4 | 104018 | Spring תשפ"ה / 2025 | א' | `2025s_final_A` | Ingest solution |
| מבחן5 | 104041 | Winter תשפ"ו / 2026 | ב' | `2026w_final_B` | Ingest solution |
| מבחן6 | 104042 | Winter / 2024 | א' | `2024w_final_A` | **SKIP** — already in KB (identical ids Q7a..Q9c) |

## Steps
1. **Ingest** `scripts/ingest_exam.ts` on each new solution PDF → writes `lib/kb/solutions/<slug>.json`.
   - מבחן4/5 solution PDFs have a Hebrew text layer but `extractExamStructure` finds no point markers → vision fallback for ids/points. Point-total may not hit 100; review warnings.
2. **Index** `scripts/index_kb.ts` → embeds ALL solution JSONs, upserts to Pinecone, bumps `generation.json`.
3. **Test (grade without solution)** — feed student-answer scans (the `שאלה*.jpg` / `אמרקאי*.jpg` files, which contain NO solution) through the agent (`scripts/test_agent.ts` / `test_grade.ts`). Agent must Parse → Retrieve the newly-indexed solution → Grade. Report what worked / failed.

## Cleanup
- Remove temp `scripts/_dump_tmp.ts`. ✓ (all temp probe/skeleton scripts removed)

## Outcome (2026-07-22)
- **Parser fix** (`examStructure.ts`): added a second, logical-order / two-section pass that
  fires only when the visual-order pass finds nothing. Old exams unchanged (2023w→56,
  2025w→100 ✓ regression-verified). 0 LLM tokens to build + verify.
- **מבחן4** (`2025s_final_A.json`): re-ingested clean — **TOTAL 100 ✓**, 13 questions, no dups.
- **מבחן5** (`2026w_final_B.json`): re-ingested **87/101** — vision drops 2 MC questions (Q5,Q6)
  off the dense page 4; a retry reproduced the same miss (likely the 3000 `maxTokens` cap).
  This exam's true total is 101 (open 52 + MC 49). **Open (Q1,Q2) fully present.**
- **Re-index**: 44 clean live vectors; superseded vectors filtered by generation.
- **Retriever exam-scoping** (new): the test exposed that retrieval had no exam scope, so a
  shared question id / near-duplicate question matched the wrong exam (מבחן5 `1א` graded
  0/4 instead of 5/5). Fix: central exam registry `lib/kb/exams.ts`, `exam` filter through
  `retrieve → findSemantic/findExact`, threaded `orchestrator → /api/execute`, new
  `/api/exams`, and an exam picker in the UI. Scoped re-test: `1א` → **5/5 ✓**, `1b` worked
  solution → **10/10 ✓**. `tsc --noEmit` clean.

## Known gaps / follow-ups
- מבחן5 Q5,Q6 missing (87/101) — needs a per-page vision robustness fix (raise `maxTokens`
  for dense pages, or re-query only the skeleton ids a page dropped), then one re-ingest.
- Parser over-splits a single answer into statement + solution fragments; the stray
  statement fragment can match a wrong (same-exam) question and escalate. Fails safe.
