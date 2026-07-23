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
    grader_samples_tf_mc: int = 1      # T/F + MC: circled letter, all-or-nothing -> one call
    high_point_threshold: int = 15     # a question worth >= this uses grader_samples_high
    disagreement_frac: float = 0.25    # escalate when spread > max(floor, frac * max_points)
    disagreement_floor: float = 1.5

    # --- Reflection (orchestrator + reflector stages) ---
    max_revise_passes: int = 2         # early-exit on APPROVE

    # --- Parser input (render) ---
    render_max_pages: int = 12         # pages rendered per upload; 18-page booklets need >12

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
        grader_samples_tf_mc=_int("CHECKMATE_GRADER_SAMPLES_TFMC", 1, 1, 5),
        grader_samples_high=_int("CHECKMATE_GRADER_SAMPLES_HIGH", 5, 1, 7),
        max_revise_passes=_int("CHECKMATE_MAX_REVISE_PASSES", 2, 0, 3),
        render_max_pages=_int("CHECKMATE_MAX_PAGES", 12, 1, 60),
    )


# One instance per process; stages import CONFIG.
CONFIG = load_config()
