# PLAN — Migrate CheckMate to pure Python on Vercel

Date: 2026-07-22

## Goal
Replace the TypeScript / Next.js app with a **pure-Python** app that still **deploys on Vercel**.
Chosen GUI: **FastAPI + Jinja server-rendered HTML** (reuse the existing CSS). No React/Next.

## Why this shape
- Vercel runs Python natively (Fluid Compute, Python 3.13/3.14, 300s timeout); **FastAPI is
  zero-config supported**. Backend is a clean lift.
- A browser runs HTML/JS, not Python — so "pure Python" = all *code* is Python, the browser
  gets Python-rendered HTML (Jinja). Streamlit/Gradio were rejected: they need a persistent
  stateful server and do **not** fit Vercel.
- The trial already proved the port is faithful: `exam_structure.py` matches the TS
  byte-for-byte, `grader.py` runs. Risk is mostly plumbing + deploy, not logic.

## Target structure
```
api/index.py             # FastAPI `app` — the Vercel entrypoint (all routes)
checkmate/
  env.py                 # LLMOD_* / PINECONE_* loading            (← lib/env.ts)
  llm.py                 # chat(), embed(), extract_json(), StepLog (← lib/llm.ts)
  pdf.py                 # render_pdf_to_images() via pymupdf        (← lib/agent/pdf.ts)
  parser.py              # run_parser()                              (← lib/agent/parser.ts)
  retriever.py           # retrieve(), retrieve_notes() + exam scope (← lib/agent/retriever.ts)
  grader.py              # run_grader()                    (already ported — move in)
  reflector.py           # run_reflector()                           (← lib/agent/reflector.ts)
  orchestrator.py        # run_agent()                               (← lib/agent/orchestrator.ts)
  mock_agent.py          # deterministic fallback                    (← lib/mockAgent.ts)
  kb/
    exam_structure.py    # already ported — move in
    exams.py             # EXAMS + exam_options()                    (← lib/kb/exams.ts)
    pinecone.py          # kb_index(), ensure_index()                (← lib/kb/pinecone.ts)
    solutions/*.json     # REUSED unchanged
    generation.json      # REUSED unchanged
scripts/
  ingest_exam.py         # (← scripts/ingest_exam.ts; ingest_solution.py is a reference)
  index_kb.py            # (← scripts/index_kb.ts)
  test_grade.py / test_agent.py
templates/index.html     # Jinja port of app/page.tsx
static/styles.css        # ← app/globals.css ; static/architecture.png ← public/
requirements.txt         # fastapi, uvicorn, jinja2, python-multipart, openai, pinecone, pymupdf
vercel.json              # rewrite /(.*) → /api/index
```

## Endpoints (parity with current Next API)
- `GET  /`                    → Jinja page (upload form + results)
- `POST /api/execute`         → multipart {file, instructions?, exam?} → run agent
- `GET  /api/exams`           → exam picker list
- `GET  /api/team_info` · `GET /api/agent_info` · `GET /api/model_architecture`

## Phased execution
1. **Scaffold + core** — dirs, `requirements.txt`, `env.py`, `llm.py`, `pdf.py`, `kb/pinecone.py`,
   `kb/exams.py`; move `exam_structure.py` + `grader.py` into `checkmate/`.
2. **Pipeline** — `parser.py`, `retriever.py` (with exam scoping), `reflector.py`,
   `orchestrator.py`, `mock_agent.py`.
3. **Scripts** — `ingest_exam.py`, `index_kb.py`, `test_grade.py`, `test_agent.py`.
4. **Web** — `api/index.py` (FastAPI + all endpoints), `templates/index.html`, `static/`.
5. **Config** — `vercel.json`, `requirements.txt`; local `uvicorn` smoke test.
6. **Validate** (token-aware):
   - free/deterministic: `exam_structure` parity (done), `pdf` render, `retriever` exact-id + scoping.
   - one live agent run on a single scan (~5–10k tokens) to confirm end-to-end parity with TS.
7. **Deploy check** — Vercel build/deploy; **verify pymupdf native wheel installs** (main risk).
8. **Cutover** — keep TS intact on `main`; do all of the above on a branch. Switch only after
   Python validates + deploys.

## Risks / watch
- **pymupdf** native wheel on Vercel's Python runtime — validate early in a deploy.
- FastAPI ASGI entrypoint specifics on Vercel (`api/index.py` exposing `app` + rewrite).
- Full-booklet runs vs the 300s ceiling — same constraint as TS (`CHECKMATE_MAX_PAGES`).
- Token cost is only in step 6's one live run; writing code is free of LLMOD spend.

## Decisions
- **Branch**: do the migration on a new branch (e.g. `python-migration`); `main` keeps the
  working TS app until Python is validated and deployed.
- **TS removal**: defer — remove Next/React files only after cutover is confirmed.
