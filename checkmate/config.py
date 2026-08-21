"""Central tuning config for the agent's model calls (section 6).

Lifted from literals that were scattered across the stages. The values here are the CURRENT
effective ones -- lifting changes no behaviour. Tune from HERE, one variable at a time
(section 6.6), and the active config is logged into every run trace and the eval report so a
number is always attributable to a config.

This is shared config consumed INSIDE the existing stages (parser/grader/reflector/
orchestrator) -- not a new pipeline stage.

Temperature note: gpt-5.4-mini REJECTS any explicit `temperature` (HTTP 400,
"gpt-5 models don't support temperature"). The gateway default (~1.0, verified to vary
across identical calls) is the only option, so temperature is documented here but is NOT a
knob -- the "reflector runs cold" idea is not achievable on this model.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AgentConfig:
    # --- Grader self-consistency (grader stage), adaptive N by question type (6.2) ---
    grader_samples: int = 3            # open/proof questions
    grader_samples_high: int = 5       # high-point open questions (>= high_point_threshold)
    grader_samples_tf_mc: int = 3      # T/F + MC: median-of-3 -- a single call proved unstable
                                       # (eval: MC-1 flipped 7->0 between identical runs); the
                                       # items are ~250-token calls, so the safety is ~free
    high_point_threshold: int = 15     # a question worth >= this uses grader_samples_high
    disagreement_frac: float = 0.25    # escalate when spread > max(floor, frac * max_points)
    disagreement_floor: float = 1.5

    # --- Reflection (orchestrator + reflector stages), 7.3 ---
    max_revise_passes: int = 2               # cap for high-stakes open questions
    reflection_passes_open: int = 1          # smaller open questions get one pass
    reflection_high_threshold: int = 15      # >= this many points -> full max_revise_passes
    reflect_tf_mc: bool = False              # T/F + MC have no argument -> skip reflection
    max_reflection_tokens_per_q: int = 2200  # cumulative reflector budget/question; over -> stop

    # --- Cost model + guardrail (6.2/6.3). Rates are deliberately HIGH placeholders (~2-3x a
    # realistic mini-tier price) so the ceiling errs toward aborting early, never overspending.
    # Back-fill real gateway rates when known; token counts in the report are exact either way.
    price_input_per_1k: float = 0.00075    # $ per 1k input tokens (incl. vision image tokens)
    price_output_per_1k: float = 0.00300   # $ per 1k output tokens
    price_embed_per_1k: float = 0.00002    # $ per 1k embedding tokens (text-embedding-3-small)
    max_run_cost_usd: float = 0.75         # abort a run that would exceed this

    # --- Parser input (render) ---
    render_max_pages: int = 24         # pages rendered per upload; real booklets run 18-20
                                       # pages, and the parallel parser makes 24 as fast as 12

    # --- Run ceilings (6.3) ---
    max_run_seconds: int = 240         # wall-clock guard under Vercel's 300s hard limit:
                                       # stop before the next question past this point
    parse_deadline_seconds: int = 120  # the Parser's own share of that budget: past this
                                       # point no NEW vision call is started, so a long
                                       # upload cannot consume the whole run before a
                                       # single question has been graded

    # --- Token budgets (per stage), by question type (6.4) ---
    parser_max_tokens: int = 2500
    grader_max_tokens: int = 1800      # open: generous, so it can walk the argument step by step
    grader_max_tokens_tf_mc: int = 250  # T/F + MC: just the circled answer + one-line reason
    reflector_max_tokens: int = 900

    # Documented, not tunable (see module docstring).
    temperature: str = "gateway-default(non-tunable)"

    def to_log(self) -> dict:
        return asdict(self)


def load_config() -> AgentConfig:
    """Build the active config, honouring the few shell-tunable env overrides."""
    return AgentConfig(
        grader_samples=_int("CHECKMATE_GRADER_SAMPLES", 3, 1, 5),
        grader_samples_tf_mc=_int("CHECKMATE_GRADER_SAMPLES_TFMC", 3, 1, 5),
        grader_samples_high=_int("CHECKMATE_GRADER_SAMPLES_HIGH", 5, 1, 7),
        max_revise_passes=_int("CHECKMATE_MAX_REVISE_PASSES", 2, 0, 3),
        render_max_pages=_int("CHECKMATE_MAX_PAGES", 24, 1, 60),
        max_run_seconds=_int("CHECKMATE_MAX_RUN_SECONDS", 240, 30, 290),
        parse_deadline_seconds=_int("CHECKMATE_PARSE_DEADLINE", 120, 15, 280),
    )


# One instance per process; stages import CONFIG.
CONFIG = load_config()
