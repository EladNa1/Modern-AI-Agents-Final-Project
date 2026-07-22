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

# Make the `checkmate` package importable when Vercel runs this file as api/index.py.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from checkmate.kb.exams import exam_options  # noqa: E402
from checkmate.mock_agent import run_mock_agent  # noqa: E402
from checkmate.models import ImageInput  # noqa: E402
from checkmate.orchestrator import run_agent  # noqa: E402
from checkmate.pdf import render_pdf_to_images  # noqa: E402

app = FastAPI(title="CheckMate")
app.mount("/static", StaticFiles(directory=os.path.join(_ROOT, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_ROOT, "templates"))

ALLOWED = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _bad(error: str) -> dict:
    return {"status": "error", "error": error, "response": None, "steps": []}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/exams")
async def api_exams():
    return {"exams": exam_options()}


@app.get("/api/team_info")
async def team_info():
    return {
        "group_batch_order_number": "TBD_TBD",
        "team_name": "CheckMate",
        "students": [
            {"name": "Elad Nahalieli", "email": "eladna97@gmail.com"},
            {"name": "Shiri Haboob", "email": "TODO@campus.technion.ac.il"},
            {"name": "Yaron Mozes", "email": "TODO@campus.technion.ac.il"},
        ],
    }


@app.get("/api/agent_info")
async def agent_info():
    example_source = "calc1_2024w_final_A.pdf (612 KB)"
    example = run_mock_agent(example_source)
    return {
        "description": (
            "CheckMate is an autonomous agent that grades Calculus 1 (Hedva 1) exams. You upload "
            "a scanned exam (PDF, Word, or images); it reads every page with OCR, splits the exam "
            "into questions, and grades each on the student's ACTUAL method — awarding partial "
            "credit and writing per-question feedback. Grades are grounded in the retrieved "
            "official solution and rubric, not invented. Ambiguous handwriting or low-confidence "
            "grades are escalated to a human teacher, never silently guessed. It returns the "
            "graded exam with annotations plus the total. Internally it is a Reflection Agent with "
            "four logged modules — Parser (vision OCR, splits the exam), Retriever (Pinecone vector "
            "RAG that grounds each grade in the matching official solution AND in relevant course "
            "lecture notes), Grader (few-shot, partial credit), and Reflector (self-critique that "
            "approves, revises, or escalates) — matching the model_architecture diagram. What it "
            "CANNOT do: it does not set exam questions, does not tutor, and does not fabricate a rubric."
        ),
        "purpose": (
            "Cut the hours TAs spend grading Calculus 1 exam stacks by hand, and give every student "
            "instant, consistent, fair feedback on every question."
        ),
        "prompt_template": {
            "template": (
                "Upload a scanned Calculus 1 exam file (PDF / Word / image) to POST /api/execute as "
                "multipart form-data under the field `file`. No free-text prompt is required. "
                '(A JSON body { "prompt": "<exam reference>" } is also accepted.)'
            )
        },
        "prompt_examples": [{
            "prompt": f"POST /api/execute  (multipart)  file={example_source}",
            "full_response": example["response"],
            "steps": example["steps"],
        }],
    }


@app.get("/api/model_architecture")
async def model_architecture():
    return FileResponse(os.path.join(_ROOT, "static", "architecture.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/architecture.png")
async def architecture_png():
    return FileResponse(os.path.join(_ROOT, "static", "architecture.png"), media_type="image/png")


@app.post("/api/execute")
async def execute(request: Request):
    ct = request.headers.get("content-type", "")

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
                f'Unsupported file type "{ext}". Accepted: PDF, Word (.doc/.docx), or image '
                f'(.png/.jpg/.webp).'), status_code=400)

        data = await upload.read()
        kb = round(len(data) / 1024)
        source_label = f"{name} ({kb} KB)"

        # Images go straight to the vision Parser. PDFs render to per-page PNGs. Word has no
        # renderer yet, so it takes the mock fallback inside run_agent (images=[]).
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

        result = run_agent(images, instructions, source_label, exam)
        return JSONResponse(result, status_code=400 if result["status"] == "error" else 200)

    # JSON path — { "prompt": "…", "instructions"?: "…" } — required-contract shape. No scan
    # image here, so the deterministic mock keeps the contract working.
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_bad("Could not read the request body."), status_code=400)
    prompt = body.get("prompt") if isinstance(body.get("prompt"), str) else ""
    instructions = body.get("instructions") if isinstance(body.get("instructions"), str) else ""
    result = run_mock_agent(prompt, instructions)
    return JSONResponse(result, status_code=400 if result["status"] == "error" else 200)
