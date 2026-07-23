"""LLMod.ai gateway client (OpenAI-compatible) + a step recorder for the trace.
Port of lib/llm.ts.
"""
from __future__ import annotations

import json
import re

from openai import OpenAI

from .env import LLMOD_BASE_URL, LLMOD_EMBED_MODEL, LLMOD_KEY, LLMOD_MODEL
from .models import ImageInput, Step, Usage

_client: OpenAI | None = None


def _client_singleton() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=LLMOD_BASE_URL, api_key=LLMOD_KEY)
    return _client


def chat(system: str, user: str, images: list[ImageInput] | None = None,
         max_tokens: int = 1500, temperature: float | None = None,
         json_mode: bool = False, model: str | None = None) -> tuple[str, Usage]:
    """One chat completion (optionally multimodal). Images ride in the user turn as
    data-URI image_url parts, exactly like the TS client. `model` overrides LLMOD_MODEL for
    this call (used to route grading/reflection to a stronger model)."""
    parts: list[dict] = [{"type": "text", "text": user}]
    for img in images or []:
        parts.append({"type": "image_url", "image_url": {"url": img.data_url, "detail": img.detail}})

    kwargs: dict = {
        "model": model or LLMOD_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": parts},
        ],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = _client_singleton().chat.completions.create(**kwargs)
    u = resp.usage
    usage: Usage = {
        "prompt": getattr(u, "prompt_tokens", 0) or 0,
        "completion": getattr(u, "completion_tokens", 0) or 0,
        "total": getattr(u, "total_tokens", 0) or 0,
    }
    return (resp.choices[0].message.content or ""), usage


def embed(inputs: str | list[str]) -> list[list[float]]:
    """Text embeddings via the same gateway. Accepts one string or a batch; returns one
    vector per input, in order."""
    batch = inputs if isinstance(inputs, list) else [inputs]
    resp = _client_singleton().embeddings.create(model=LLMOD_EMBED_MODEL, input=batch)
    # The API may return items out of order -- sort by index to be safe.
    ordered = sorted(resp.data, key=lambda d: d.index)
    return [d.embedding for d in ordered]


def extract_json(text: str):
    """Lenient JSON extraction -- models sometimes wrap JSON in prose or ```json fences.
    Returns the first balanced {...} object parsed, or None."""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(candidate)):
        c = candidate[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class StepLog:
    """Accumulates brief-shaped Steps for the /api/execute trace. One instance per run."""

    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.usage: Usage = {"prompt": 0, "completion": 0, "total": 0}

    def add(self, module: str, system: str, user: str, response,
            pattern: str | None = None, usage: Usage | None = None) -> None:
        self.steps.append(Step(
            module=module, pattern=pattern,
            prompt={"System_prompt": system, "User_prompt": user},
            response=response,
        ))
        if usage:
            for k in self.usage:
                self.usage[k] += usage.get(k, 0)
