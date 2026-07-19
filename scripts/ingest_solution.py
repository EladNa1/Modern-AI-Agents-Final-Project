"""
Dev-only ingest (Phase 2): render an exam question + its official solution to
images, vision-OCR them cleanly (Hebrew + LaTeX), and write an app-bundled JSON
grounding file the Retriever reads at runtime.

Why images: the course PDFs store Hebrew in a legacy font, so plain text
extraction returns mojibake. Rendering + vision OCR recovers clean text.

Run:  python scripts/ingest_solution.py
Requires LLMOD_KEY (loaded from .env.local). scripts/ is .vercelignored (dev-only).
"""
import os, io, json, base64, sys
import fitz  # pymupdf
from openai import OpenAI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env_local():
    p = os.path.join(ROOT, ".env.local")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

load_env_local()
BASE_URL = os.getenv("LLMOD_BASE_URL", "https://api.llmod.ai/v1")
API_KEY  = os.getenv("LLMOD_KEY")
MODEL    = os.getenv("LLMOD_MODEL", "MB5R2CF-azure/gpt-5.4-mini")
if not API_KEY:
    sys.exit("Set LLMOD_KEY (see .env.example / .env.local).")
client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

def render_page_png(pdf_rel, page_index, dpi=200):
    doc = fitz.open(os.path.join(ROOT, pdf_rel))
    pix = doc[page_index].get_pixmap(dpi=dpi)
    png = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png).decode()

def ocr(png_b64, what):
    prompt = (
        f"This image is a page from a Technion Calculus 1 exam ({what}). "
        "Transcribe it faithfully into clean text. Preserve the Hebrew exactly and "
        "render all mathematics in LaTeX (inline $...$ or display $$...$$). "
        "Do NOT solve, summarize, or add commentary. Output only the transcription."
    )
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{png_b64}", "detail": "high"}},
        ]}],
    )
    u = resp.usage
    return resp.choices[0].message.content, (u.prompt_tokens, u.completion_tokens, u.total_tokens)

EXAM = "Data/מבחני עבר חדווא 1/2023w/104041 2023w final A.pdf"
SOL  = "Data/מבחני עבר חדווא 1/2023w/104041 2023w final A sol.pdf"
PAGE = 7  # 0-indexed: Q3(c) is on page 8 of both files

def main():
    print("Rendering + OCR exam page…")
    q_text, q_usage = ocr(render_page_png(EXAM, PAGE), "the question")
    print("Rendering + OCR solution page…")
    s_text, s_usage = ocr(render_page_png(SOL, PAGE), "the official solution")
    total = tuple(a + b for a, b in zip(q_usage, s_usage))
    print("tokens prompt/completion/total:", total)

    entry = {
        "exam": "2023w final A",
        "course": "104041",
        "source": {"exam_pdf": EXAM, "solution_pdf": SOL, "page": PAGE + 1},
        "questions": {
            "Q3c": {
                "id": "Q3c",
                "points": 9,
                "topic": "improper/extended limit via FTC + L'Hopital",
                "problem": q_text.strip(),
                "official_solution": s_text.strip(),
                "final_answer": "2/3",
                "notes": "For x<0, sqrt(x^6)=|x|^3=-x^3 — a sign subtlety; a lost sign yields -2/3 instead of the correct +2/3.",
            }
        },
    }
    out_dir = os.path.join(ROOT, "lib", "kb", "solutions")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "2023w_final_A.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    sys.stdout.reconfigure(encoding="utf-8")
    print("wrote", out)
    print("\n--- PROBLEM (Q3c) ---\n", q_text.strip()[:600])
    print("\n--- SOLUTION (Q3c) ---\n", s_text.strip()[:900])

if __name__ == "__main__":
    main()
