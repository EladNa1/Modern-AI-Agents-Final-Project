"""Send a slide PNG to the llmod.ai gateway and ask the model to explain it."""
import os, sys, base64
from openai import OpenAI

BASE_URL = os.getenv("LLMOD_BASE_URL", "https://api.llmod.ai/v1")
API_KEY  = os.getenv("LLMOD_KEY")  # set in env / .env.local — never hardcode (old key was rotated)
MODEL    = os.getenv("LLMOD_MODEL", "MB5R2CF-azure/gpt-5.4-mini")
if not API_KEY:
    sys.exit("Set LLMOD_KEY in your environment (see .env.example).")

img_path = sys.argv[1] if len(sys.argv) > 1 else "slide1.png"
img_b64 = base64.b64encode(open(img_path, "rb").read()).decode()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
resp = client.chat.completions.create(
    model=MODEL,
    max_tokens=1500,
    messages=[{
        "role": "user",
        "content": [
            {"type": "text",
             "text": "Transcribe EVERYTHING you can read in this image as raw text. "
                     "Output only the transcription of all text and handwriting you can make out, "
                     "in reading order, preserving Hebrew and math as written. "
                     "Do NOT summarize, do NOT explain, do NOT add commentary. "
                     "For parts that are illegible or crossed out, write [illegible]. Output plain text only."},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}},
        ],
    }],
)
reply = resp.choices[0].message.content
u = resp.usage
with open("reply.txt", "w", encoding="utf-8") as f:
    f.write(reply + f"\n\nTokens: {u.prompt_tokens}/{u.completion_tokens}/{u.total_tokens}")
sys.stdout.reconfigure(encoding="utf-8")
print("REPLY:\n" + reply)
print(f"\nTokens prompt/completion/total: {u.prompt_tokens} {u.completion_tokens} {u.total_tokens}")
