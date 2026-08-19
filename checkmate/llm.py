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
    text = resp.choices[0].message.content or ""
    # The mini sometimes emits C0 control characters in place of LaTeX backslash commands
    # (\x1asqrt{12} for \sqrt{12}, \x17_0^1 for \int_0^1); they render as tofu boxes and
    # pollute stored grades. Recover the recognizable patterns, drop the rest (keep \n\t).
    # The model also writes them as JSON escapes (""), which survive a raw-byte strip
    # and get re-created by json.loads later -- normalize those to a raw byte first.
    text = re.sub(r"\\u00(?:0[0-8bcBC]|0[eE]|1[0-9a-fA-F])", "\x1a", text)
    _C0 = r"[\x00-\x08\x0b\x0c\x0e-\x1f]"
    text = re.sub(_C0 + r"(?=sqrt|int|frac|sum|lim|pi\b)", "\\\\", text)
    text = re.sub(_C0 + r"(?=_)", r"\\int", text)   # \x17_0^1 -> \int_0^1
    text = re.sub(_C0 + r"(?=\d)", "√", text)        # \x1a12  -> √12
    text = re.sub(_C0, "", text)
    return text, usage


def embed(inputs: str | list[str], log: "StepLog | None" = None) -> list[list[float]]:
    """Text embeddings via the same gateway. Accepts one string or a batch; returns one
    vector per input, in order. Pass `log` to have the embedding tokens counted into the
    run's cost accounting (they are cheap but not free)."""
    batch = inputs if isinstance(inputs, list) else [inputs]
    resp = _client_singleton().embeddings.create(model=LLMOD_EMBED_MODEL, input=batch)
    if log is not None:
        log.embed_tokens += getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0
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
        self.by_module: dict[str, Usage] = {}  # per-stage usage, for cost-by-stage (6.2)
        self.embed_tokens: int = 0             # embedding tokens (Retriever queries)

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
            m = self.by_module.setdefault(module, {"prompt": 0, "completion": 0, "total": 0})
            for k in m:
                m[k] += usage.get(k, 0)

    def cost_by_stage(self) -> dict:
        """Estimated $ per stage + total, from the configured token prices (6.2).
        Embedding tokens (Retriever queries) are counted too -- cheap, but not free."""
        from .config import CONFIG
        stages = {mod: round(cost_usd(u), 4) for mod, u in self.by_module.items()}
        if self.embed_tokens:
            stages["Embeddings"] = round(self.embed_tokens / 1000 * CONFIG.price_embed_per_1k, 6)
        stages["total"] = round(sum(stages.values()), 4)
        return stages


def cost_usd(usage: Usage) -> float:
    """Estimate the $ cost of one usage record from the configured token prices."""
    from .config import CONFIG
    return (usage.get("prompt", 0) / 1000 * CONFIG.price_input_per_1k
            + usage.get("completion", 0) / 1000 * CONFIG.price_output_per_1k)
