# PLAN — CheckMate UI + API (Vercel)

## Goal
Deck-styled web interface + all 4 required API endpoints, deployable to Vercel.
`/api/execute` = **mock agent** (correctly-shaped `response` + realistic `steps[]`,
no real LLM/OCR/SymPy yet). Real grading wired in later.

## Decisions (locked)
- Backend scope: **mock agent** — endpoints real & correctly shaped, grading simulated.
- Stack: **Next.js App Router** on Vercel.

## Visual language (from CheckMate_DemoDay.pptx)
| Token | Hex | Use |
|-------|-----|-----|
| navy | `#1F2430` | dark text / headers |
| indigo (brand) | `#534AB7` | primary accent, buttons |
| green | `#1D8F73` | verified / success |
| amber | `#BA7517` | warning / escalate |
| muted | `#64708A` / `#8A93A8` | secondary text |
| tint | `#F3F4FB` / `#E4E6F2` | panels / chips |
| white | `#FFFFFF` | surfaces |
- Headings: serif (Cambria → Georgia). Body: system sans (Calibri-like).

## Required endpoints (names exact)
- `GET  /api/team_info`          — group/team/students JSON
- `GET  /api/agent_info`         — description, purpose, prompt_template, prompt_examples[]
- `GET  /api/model_architecture` — PNG (Content-Type image/png)
- `POST /api/execute`            — `{status,error,response,steps[]}`; steps = module/prompt/response

## GUI requirements (root `/`)
- Textarea for prompt/task, "Run Agent" button → POST /api/execute
- Display final `response`
- Display full `steps[]` trace (module + prompt + response), collapsible
- No auth guards
- Module names consistent with architecture diagram: Transcriber, Retriever, Verifier, Grader, Reflector, Supervisor

## Steps
1. Scaffold Next.js app (app router, TS, no Tailwind — inline CSS module / globals for tight control).
2. Build `/` page: deck-styled shell, prompt textarea, Run button, response card, steps accordion.
3. API routes: team_info, agent_info, execute (mock, realistic per-question grading trace), model_architecture (serve PNG).
4. Generate architecture PNG (Supervisor/Transcriber/Retriever/Verifier/Grader/Reflector) into `public/`.
5. `npm run build` locally — verify.
6. Deploy: `vercel` (preview) → confirm → `vercel --prod`.
7. Report Vercel URL.

## Open later (real agent)
LLMod.ai gpt-5.4-mini calls, OCR of scan, Pinecone RAG, SymPy verify, Supabase persistence, $13 budget guard.
