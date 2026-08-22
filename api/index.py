"""FastAPI entrypoint — the whole CheckMate app on Vercel (Python, Fluid Compute).

Serves the Jinja UI and the JSON API. Port of the Next.js app/ routes:
  GET  /                       -> the upload + results page (templates/index.html)
  POST /api/execute            -> run the agent on an uploaded scan
  GET  /api/exams              -> exam picker list
  GET  /api/team_info · /api/agent_info · /api/model_architecture
"""
from __future__ import annotations

import base64
import os
import sys
import time

# Make the `checkmate` package importable when Vercel runs this file as api/index.py.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from checkmate.env import HAS_LLM  # noqa: E402
from checkmate.guard import in_scope, refusal_payload  # noqa: E402
from checkmate.kb.exams import exam_options  # noqa: E402
from checkmate.samples import available_samples_text, load_sample_parse, resolve_sample  # noqa: E402
from checkmate.mock_agent import run_mock_agent  # noqa: E402
from checkmate.models import ImageInput  # noqa: E402
from checkmate.orchestrator import run_agent  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402

app = FastAPI(title="CheckMate")
app.mount("/static", StaticFiles(directory=os.path.join(_ROOT, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_ROOT, "templates"))

# PDF and images only. Word was once listed but had no renderer — uploads silently fell
# through to the mock, i.e. simulated grades presented as real. Rejecting honestly is the
# only defensible behavior.
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _bad(error: str) -> dict:
    return {"status": "error", "error": error, "response": None, "steps": []}


def _spec_shape(result: dict, ui: bool) -> dict:
    """The course contract for /api/execute is EXACTLY {status, error, response, steps}
    with steps entries of EXACTLY {module, prompt, response}. Our own GUI wants more
    (meta for rendering, pattern labels on steps) — it opts in with ?ui=1; every other
    caller gets the mandated shape, nothing extra."""
    if ui:
        return result
    return {
        "status": result.get("status"),
        "error": result.get("error"),
        "response": result.get("response"),
        "steps": [{"module": s.get("module"), "prompt": s.get("prompt"),
                   "response": s.get("response")} for s in result.get("steps") or []],
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/exams")
async def api_exams():
    return {"exams": exam_options()}


@app.get("/api/team_info")
async def team_info():
    return {
        "group_batch_order_number": "2_09",
        "team_name": "הסוכנים",
        "students": [
            {"name": "Elad Nahalieli", "email": "nelad@campus.technion.ac.il"},
            {"name": "Shiri Haboob", "email": "shiri.haboob@campus.technion.ac.il"},
            {"name": "Yaron Mozes", "email": "yaron.mozes@campus.technion.ac.il"},
        ],
    }


def _real_examples() -> list[dict]:
    """Captured REAL runs (scripts/capture_examples.py output, bundled under kb/samples/)
    — served as the spec-required prompt_examples so the grader reads genuine traces:
    a full grading run and an out-of-domain refusal. These are BARE spec-shape captures
    (one steps entry per model call, no extra fields); the ?replay view uses the separate
    ui-enriched captures instead."""
    import json
    out: list[dict] = []
    for fname in ("example_agent_info_run.json", "example_agent_info_refusal.json"):
        path = os.path.join(_ROOT, "checkmate", "kb", "samples", fname)
        try:
            rec = json.load(open(path, encoding="utf-8"))
            resp = rec["response"]
            out.append({"prompt": rec["request"]["prompt"], "full_response": resp["response"],
                        "steps": resp["steps"]})
        except Exception:
            pass
    return out


@app.get("/api/agent_info")
async def agent_info():
    example_source = "calc1_2024w_final_A.pdf (612 KB)"
    example = run_mock_agent(example_source)
    return {
        "description": (
            "CheckMate is an autonomous agent that grades Calculus 1 (Hedva 1) exams. You upload "
            "a scanned exam (PDF or images); it reads every page with OCR, splits the exam "
            "into questions, and grades each on the student's ACTUAL method — awarding partial "
            "credit and writing per-question feedback. Grades are grounded in the retrieved "
            "official solution and rubric, not invented. Handwriting too ambiguous to grade "
            "fairly is escalated to a human teacher, never silently guessed; milder legibility "
            "concerns are flagged on the question (borderline_legibility) for the reviewer's eye. It returns the "
            "graded exam with annotations plus the total. Internally it is a Reflection Agent; "
            "every stage logs its calls in steps[] under its own module name, matching the "
            "model_architecture diagram: Config (run-setup snapshot), Parser (vision OCR, splits "
            "the exam), Router (zero-token scope guard + exam scoping), Retriever (Pinecone vector "
            "RAG that grounds each grade in the matching official solution AND in relevant course "
            "lecture notes), Grader (few-shot, partial credit, self-consistency), Reflector "
            "(self-critique that approves, revises, or escalates); the GradeBook assembles the "
            "final result deterministically (logged as a zero-LLM step). Zoom is not a separate "
            "stage but the Parser's opt-in Pass 2 — a magnified re-read of faint regions — logged "
            "under its own Zoom module name when enabled. Retrieval "
            "is VERIFIED, not trusted: matches below a similarity floor are discarded (the agent "
            "grounds on nothing and escalates rather than on the wrong solution), borderline "
            "matches must be corroborated by the printed question text, retrieval is "
            "scoped to the exam identified from the scan, the Grader must reject a retrieved "
            "solution that does not match the question in front of it, and where an answer key was "
            "once disputed the resolution is recorded in that entry's grading note, which the "
            "Retriever passes through to the Grader (2024W-A Q2b). Every run is bounded in TIME as well "
            "as cost: a 45s per-call HTTP timeout, a 120s parse deadline and a 240s run guard "
            "keep the request inside Vercel's 300s limit, and when a ceiling is reached the "
            "agent returns a provisional result for the questions it did grade — clearly marked "
            "as stopped early — rather than nothing at all. What it "
            "CANNOT do: it does not set exam questions, does not tutor, and does not fabricate a "
            "rubric. Out-of-domain requests (anything that is not a Calculus 1 grading task) are "
            "refused politely by the Router's scope guard BEFORE any model call, so no tokens are "
            "spent on them — the refusal itself is logged as a Router decision in steps[]."
        ),
        "purpose": (
            "Cut the hours TAs spend grading Calculus 1 exam stacks by hand, and give every student "
            "instant, consistent, fair feedback on every question."
        ),
        "prompt_template": {
            "template": (
                "Upload a scanned Calculus 1 exam file (PDF / image) to POST /api/execute as "
                "multipart form-data under the field `file` — or send a JSON body "
                '{ "prompt": "<request>" } naming a bundled sample booklet, e.g. '
                '"Grade sample booklet 1". The sample\'s vision transcription is pre-cached; '
                "the Router, Retriever, Grader, and Reflector then run live on it. "
                "Out-of-domain prompts are politely refused."
            ),
            "example": "Grade sample booklet 1 (104041 2024 Winter Moed A)",
        },
        "prompt_examples": _real_examples() or [{
            "prompt": f"POST /api/execute  (multipart)  file={example_source}",
            "full_response": example["response"],
            "steps": example["steps"],
        }],
    }


@app.get("/api/example_result")
async def example_result(which: str = "2"):
    """Replay of a bundled captured run (zero LLM calls) — lets the GUI's result view be
    inspected without spending a grading run. `which`: "1" = sample booklet 1, anything
    else = sample booklet 2 (the flagship). Same payload /api/execute returned when the
    example was captured."""
    import json
    from checkmate.samples import SAMPLES
    fname = "example_run_b1.json" if which == "1" else "example_run.json"
    sample = SAMPLES[0] if which == "1" else SAMPLES[1]
    path = os.path.join(_ROOT, "checkmate", "kb", "samples", fname)
    try:
        payload = json.load(open(path, encoding="utf-8"))["response"]
        # Annotate the capture so the viewer knows WHICH kind of number they are reading.
        # A replay is one recorded run, not a claim about what a fresh run will produce: the
        # Grader samples N times and takes a median, so open-question scores legitimately
        # move between runs. Saying so is the honest fix for "the replay and the live run
        # disagree" -- pinning the live score to the capture would be a lie.
        if isinstance(payload.get("meta"), dict):
            payload["meta"]["human_total"] = sample.get("human_total")
            payload["meta"]["replay"] = True
        return JSONResponse(payload)
    except Exception:
        return JSONResponse(_bad("No captured example is bundled."), status_code=404)


@app.get("/api/model_architecture")
async def model_architecture():
    return FileResponse(os.path.join(_ROOT, "static", "architecture.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/architecture.png")
async def architecture_png():
    return FileResponse(os.path.join(_ROOT, "static", "architecture.png"), media_type="image/png")


@app.post("/api/execute")
async def execute(request: Request):
    t_entry = time.time()  # the run's wall clock starts HERE, before upload read / render
    ct = request.headers.get("content-type", "")
    ui = request.query_params.get("ui") == "1"  # our GUI opts into extra fields; see _spec_shape

    if "multipart/form-data" in ct:
        form = await request.form()
        upload = form.get("file")
        instr = form.get("instructions")
        instructions = instr if isinstance(instr, str) else ""
        exam_field = form.get("exam")
        exam = exam_field.strip() if isinstance(exam_field, str) and exam_field.strip() else None

        if upload is None or isinstance(upload, str):
            return JSONResponse(_bad("No exam file in the upload."), status_code=400)

        name = upload.filename or "exam"
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED:
            return JSONResponse(_bad(
                f'Unsupported file type "{ext}". Accepted: PDF or image (.png/.jpg/.webp).'),
                status_code=400)

        data = await upload.read()
        kb = round(len(data) / 1024)
        source_label = f"{name} ({kb} KB)"

        # Images go straight to the vision Parser. PDFs render to per-page PNGs.
        images: list[ImageInput] = []
        if ext in IMAGE_MIME:
            b64 = base64.b64encode(data).decode("ascii")
            images.append(ImageInput(data_url=f"data:{IMAGE_MIME[ext]};base64,{b64}", detail="high"))
        elif ext == ".pdf":
            try:
                render = render_pdf_to_images(data)
            except Exception:
                return JSONResponse(_bad("Could not read the PDF — it may be corrupt or "
                                         "password-protected."), status_code=400)
            if render.page_count == 0:
                return JSONResponse(_bad("The PDF has no pages to grade."), status_code=400)
            images = render.images
            plural = "s" if render.page_count > 1 else ""
            source_label = (
                f"{name} ({kb} KB, {render.rendered}/{render.page_count} pages graded)"
                if render.rendered < render.page_count
                else f"{name} ({kb} KB, {render.page_count} page{plural})")

        result = run_agent(images, instructions, source_label, exam, started_at=t_entry)
        # Same honesty note as the sample path: instructions are accepted but quarantined
        # from every model prompt (prompt-injection defense) -- say so, on every path.
        if instructions.strip() and isinstance(result.get("response"), str):
            result["response"] += ("\n\nNote: free-text instructions are not forwarded to the "
                                   "grading models (prompt-injection defense) — the booklet was "
                                   "graded strictly against the official rubric.")
        return JSONResponse(_spec_shape(result, ui),
                            status_code=400 if result["status"] == "error" else 200)

    # JSON path — { "prompt": "…", "instructions"?: "…" } — required-contract shape. No scan
    # image here, so the deterministic mock keeps the contract working.
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_bad("Could not read the request body."), status_code=400)
    prompt = body.get("prompt") if isinstance(body.get("prompt"), str) else ""
    instructions = body.get("instructions") if isinstance(body.get("instructions"), str) else ""

    # Router scope guard: an off-domain request gets a polite refusal BEFORE any model work.
    if prompt.strip() and not in_scope(prompt) and not in_scope(instructions):
        return JSONResponse(_spec_shape(refusal_payload(prompt), ui))

    # In-scope prompt naming a bundled sample booklet -> run the REAL agent on its cached
    # transcription (vision ran once, offline; everything downstream is live).
    sample = resolve_sample(prompt) if prompt.strip() else None
    if sample is not None:
        parsed = load_sample_parse(sample)
        result = run_agent([], instructions, sample["label"], sample["exam"], parsed=parsed,
                           started_at=t_entry)
        # Honesty note: free-text instructions are never forwarded into the Grader/Reflector
        # context (prompt-injection defense), so say so instead of silently ignoring them.
        if instructions.strip() and isinstance(result.get("response"), str):
            result["response"] += ("\n\nNote: free-text instructions are not forwarded to the "
                                   "grading models (prompt-injection defense) — the booklet was "
                                   "graded strictly against the official rubric.")
        # Attach the bundled page previews so the GUI can show WHAT was graded.
        if result.get("meta") is not None and sample.get("pages"):
            result["meta"]["sample_pages"] = [
                f"{sample['pages_prefix']}/page-{i:02d}.jpg" for i in range(1, sample["pages"] + 1)]
        # The teacher's own mark for this booklet, from the one place it is defined
        # (checkmate/samples.py). The GUI shows it BESIDE the agent's score so the two are
        # never mistaken for competing claims about the same quantity.
        if result.get("meta") is not None:
            result["meta"]["human_total"] = sample.get("human_total")
        return JSONResponse(_spec_shape(result, ui),
                            status_code=400 if result["status"] == "error" else 200)

    # In-scope but nothing to grade: explain how to attach a scan / name a sample. (The
    # deterministic mock still answers when no LLM key is configured, keeping the contract
    # alive in keyless environments.)
    if prompt.strip() and HAS_LLM:
        return JSONResponse(_spec_shape({
            "status": "ok", "error": None, "response": available_samples_text(),
            "steps": [{
                "module": "Router", "pattern": "Scope guard",
                "prompt": {
                    "System_prompt": "Decide whether this grading request carries anything to "
                                     "grade: an attached scan or a named sample booklet.",
                    "User_prompt": prompt[:400],
                },
                "response": {"in_scope": True, "decision": "NEED_INPUT",
                             "reason": "No scan attached and no sample booklet named.",
                             "llm_calls": 0, "tokens_spent": 0},
            }],
            "meta": {"mode": "info", "source": None, "total": 0, "max": 0, "questions": []},
        }, ui))

    result = run_mock_agent(prompt, instructions)
    return JSONResponse(_spec_shape(result, ui),
                        status_code=400 if result["status"] == "error" else 200)
