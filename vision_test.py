"""
Minimal vision probe against llmod.ai OpenAI-compatible gateway.
Cheapest possible check: 1x1 image, detail=low, max_tokens=10.
"""

import os, io, base64
from openai import OpenAI
from PIL import Image

BASE_URL   = os.getenv("LLMOD_BASE_URL", "https://api.llmod.ai/v1")
API_KEY    = os.getenv("LLMOD_KEY", "sk-ifkPJSYVPR6uv1ow3UfK0g")
MODEL      = os.getenv("LLMOD_MODEL", "MB5R2CF-azure/gpt-5.4-mini")

# Real 64x64 solid-red PNG. detail=low keeps token cost fixed (~85) regardless of size.
_buf = io.BytesIO()
Image.new("RGB", (64, 64), (220, 30, 30)).save(_buf, format="PNG")
RED_1x1_PNG = base64.b64encode(_buf.getvalue()).decode()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

try:
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "What color?"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{RED_1x1_PNG}",
                               "detail": "low"}},
            ],
        }],
    )
    print("VISION SUPPORTED OK")
    print("Reply:", resp.choices[0].message.content)
    if resp.usage:
        print("Tokens prompt/completion/total:",
              resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)
except Exception as e:
    print("VISION TEST FAILED")
    print(type(e).__name__, "->", e)
