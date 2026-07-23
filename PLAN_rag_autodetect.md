# PLAN — Remove the exam dropdown, auto-scope retrieval, improve notes RAG

Date: 2026-07-23

## Goal
Remove the manual "Which exam?" dropdown from the site, keep (ideally improve) retrieval
accuracy, and split RAG chunking by data type with a re-ingest where it helps.

## Hard constraint (why "pure unscoped RAG" cannot stand alone)
Different exams contain **near-identical questions** (verified: a bare "1b" exists in 3
exams; several exams have a verbatim "state the IVT" / "fixed point via IVT"). The student's
answer to a generic theorem-statement question is identical regardless of exam, so **no
retrieval tuning can infer the exam from the answer** — the disambiguating signal is not in
the content. RAG can separate exams only when a question has distinctive content
(√12 vs √11 vs ln(1.1)); it cannot for the generic ones — exactly where the Q9/IVT
mismatches happened.

## Resolution of the three selected options (layered, not contradictory)
1. **Auto-detect the exam from the scan** (PRIMARY). The booklet prints its identity —
   course number + date + מועד on the cover, and "שאלה N (X נקודות)" headers. The parser
   reads that and the run auto-scopes. Removes the manual step *without* losing accuracy.
2. **Unscoped RAG** (FALLBACK, not primary). When the upload has no identifying page
   (cropped single-question image), there is no exam id to detect → fall back to unscoped
   retrieval + the existing cross-exam consistency guard, with a lowered confidence / flag.
   Honest graceful degradation, not the default.
3. **Re-chunk + re-ingest notes** (COMPLEMENTARY). Improves *grounding* quality for the
   grader; does not affect exam disambiguation. Solutions stay one-chunk-per-question exact
   match. Costs embedding budget.
4. **Remove the dropdown** once 1+2 are in place (never before — removing it with today's
   code would regress to the unscoped bug).

## Components / changes
- `kb/exams.py`: add per-exam identity for matching — `course`, `semester` (e.g. 2025s),
  `moed` (A/B), parsed from the exam label or added as fields. Provide a
  `match_exam(course, semester|date, moed) -> exam_label` helper.
- `parser.py`: detect exam identity from the cover / first page (course #, date→semester,
  מועד). Fold into the page-1 vision call (no extra call) or one small targeted call.
  Return it on `ParseResult`.
- `orchestrator.py`: if the caller did not pass `exam`, use the parser-detected exam to
  scope; if detection failed, run unscoped (guard already handles consistency) and flag.
- `api/index.py`: keep accepting an optional `exam` (programmatic override) but the UI stops
  sending it.
- `templates/index.html`: remove the `#exam` <select> + its JS; show the *detected* exam
  read-only in the results header ("Graded as: 2025s final A · 104018 (auto-detected)").
- Ingest (`scripts/index_kb.py` / notes path): notes chunking 512–1024 tokens, 15–25%
  overlap, split on section/theorem boundaries (never mid-theorem); solutions unchanged
  (1 chunk/question, exact metadata). Re-embed notes → Pinecone.

## Budget
- Phase 1 (auto-detect + fallback + UI): ~free. Auto-detect adds a little to the existing
  parse; everything else is offline-testable with mocked LLM.
- Phase 2 (notes re-chunk + re-ingest): **costs embeddings** (embed the notes corpus once).
  Gated on explicit go-ahead. One-time, not per-grade.

## Risks / honest limits
- **Cropped uploads defeat auto-detect** (no course/date on the page) → unscoped fallback,
  which is unreliable for generic questions. The user's current single-page test images hit
  this. Full-booklet uploads (with cover) auto-detect cleanly.
- OCR of the course number / date must be reliable; treat a low-confidence detection as
  "not detected" → fallback, don't scope to a guessed exam.

## Sequencing
- **Phase 1 (free, do first):** exam-identity mapping → parser auto-detect → orchestrator
  wiring → UI dropdown removal → unscoped fallback. Validate offline (mocked LLM), one small
  live sanity run on a cover page.
- **Phase 2 (budget-gated):** notes re-chunk + re-ingest to Pinecone. Only after approval of
  the embedding spend.

## Open decisions
- Phase 2 re-ingest: approve the one-time embedding cost? (Phase 1 stands on its own.)
- Cropped-upload fallback: unscoped-and-flag (best-effort) vs escalate-the-whole-scan
  (refuse to guess)? Default proposed: unscoped-and-flag.
