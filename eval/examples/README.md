# CheckMate — stated examples (captured transcripts)

Captured against `http://127.0.0.1:8123` on 2026-08-15 15:34.

## Grade a real scanned booklet (full agent, cached vision)

- prompt: `Grade sample booklet 1`
- expected: Real Router→Retriever→Grader→Reflector run; per-question grades + trace.
- observed: status `ok`, 57 steps, 139s, est. cost $0.2553
- transcript: `01_grade_sample_booklet.json`

## Out-of-domain request — polite refusal, zero tokens

- prompt: `How do I bake sourdough bread?`
- expected: status ok, Router REFUSE step, no model calls.
- observed: status `ok`, 1 steps, 0s
- transcript: `02_offtopic_refusal.json`

## In-scope request with nothing to grade — agent asks for input

- prompt: `Please grade my calculus exam`
- expected: status ok, Router NEED_INPUT step listing the sample booklets.
- observed: status `ok`, 1 steps, 0s
- transcript: `03_grading_needs_input.json`

## Grade the second bundled booklet (same exam, different student)

- prompt: `Grade sample booklet 2`
- expected: Real run on the second student's work; human cover total is 93/100.
- observed: status `ok`, 58 steps, 137s, est. cost $0.2599
- transcript: `04_grade_sample_booklet_2.json`
