"""Generate the architecture diagram (static/architecture.png + public/architecture.png).

Single source of truth for module naming: the boxes here use EXACTLY the module names the
agent logs in steps[] (Config, Parser, Router, Retriever, Grader, Reflector, Zoom) plus the
assembly stages (Orchestrator/GradeBook), so the diagram, the trace, and the docs stay
1-to-1 -- a graded requirement of the course spec.

The layout deliberately shows the AUTONOMOUS DECISIONS (scope refusal, escalation,
revise loop, budget abort) as explicit branches -- this is a Reflection Agent, not a
linear pipeline.

Run:  python scripts/draw_architecture.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INK = "#1c1e26"
MUTED = "#5b6070"
STAGE = "#4f46e5"      # LLM stage
DET = "#0f766e"        # deterministic (zero-LLM) stage
KB = "#047857"
DECISION = "#b45309"   # autonomous decision callouts
HUMAN = "#b91c1c"
BG = "#f8f9fc"
CARD = "#e8ebf4"


def box(ax, x, y, w, h, title, sub, fc, tc="white", fs=13, sfs=8.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.010",
                                fc=fc, ec="none", zorder=3))
    ax.text(x + w / 2, y + h - 0.020, title, ha="center", va="top",
            fontsize=fs, fontweight="bold", color=tc, zorder=4)
    ax.text(x + w / 2, y + h - 0.058, sub, ha="center", va="top",
            fontsize=sfs, color=tc, alpha=0.94, zorder=4, linespacing=1.45)


def arrow(ax, p1, p2, color=MUTED, lw=2.2, con="arc3,rad=0.0"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=16,
                                 color=color, lw=lw, connectionstyle=con, zorder=2))


def label(ax, x, y, s, color=DECISION, fs=9):
    ax.text(x, y, s, ha="center", va="center", fontsize=fs, color=color,
            fontweight="bold", zorder=5,
            bbox=dict(boxstyle="round,pad=0.30", fc=BG, ec=color, lw=1.0))


def main() -> None:
    fig, ax = plt.subplots(figsize=(16, 9), dpi=160)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)

    # ---- band 1: input card above the Parser + title to its right -----------------
    ax.text(0.295, 0.99, "CheckMate — a Reflection Agent for grading Calculus 1 exams",
            fontsize=16.5, fontweight="bold", color=INK, va="top")
    ax.text(0.295, 0.945, "Orange/red = autonomous decisions (refuse · revise · escalate). "
                          "Every box is a module name logged verbatim in steps[].",
            fontsize=9.6, color=MUTED, va="top")
    box(ax, 0.03, 0.895, 0.235, 0.092, "Input — POST /api/execute",
        "scanned booklet (PDF / images)\nor a prompt naming a bundled sample scan",
        CARD, tc=INK, fs=9.6, sfs=7.6)

    # ---- band 2: the five stages --------------------------------------------------
    Y, H, W = 0.60, 0.24, 0.155
    xs = {"Parser": 0.03, "Router": 0.2225, "Retriever": 0.415,
          "Grader": 0.6075, "Reflector": 0.80}

    box(ax, xs["Parser"], Y, W, H, "Parser",
        "vision OCR — transcribe\nevery page faithfully\n\nT/F-page split (post-pass)\ncached transcript reuse",
        STAGE)
    box(ax, xs["Router"], Y, W, H, "Router",
        "scope guard: is this a\nCalc-1 grading task?\n\nresolve exam identity\n(manual · auto · unscoped)",
        DET)
    box(ax, xs["Retriever"], Y, W, H, "Retriever",
        "RAG over Pinecone\nofficial solution (per exam)\n\n+ top-k lecture notes\noffline fallbacks",
        STAGE)
    box(ax, xs["Grader"], Y, W, H, "Grader",
        "few-shot · partial credit\ngrades the student's\nactual method\n\nself-consistency ×N (median)",
        STAGE)
    box(ax, xs["Reflector"], Y, W, H, "Reflector",
        "critiques the grade against\nretrieved evidence\n\nAPPROVE · REVISE · ESCALATE\n≤N passes · token budget",
        STAGE)

    for a, b in [("Parser", "Router"), ("Router", "Retriever"),
                 ("Retriever", "Grader"), ("Grader", "Reflector")]:
        arrow(ax, (xs[a] + W + 0.004, Y + H / 2), (xs[b] - 0.004, Y + H / 2), lw=2.6)

    tags = {"Parser": ("LLM · vision", STAGE), "Router": ("deterministic · 0 tokens", DET),
            "Retriever": ("embeddings", STAGE), "Grader": ("LLM", STAGE),
            "Reflector": ("LLM", STAGE)}
    for m, (t, c) in tags.items():
        ax.text(xs[m] + W / 2, Y + H + 0.016, t, ha="center", fontsize=8.4,
                color=c, fontweight="bold")

    # input card -> Parser (straight down)
    arrow(ax, (0.1475, 0.892), (0.1475, Y + H + 0.040), lw=2.2)

    # REVISE loop under the row: Reflector -> down -> across -> up into Grader.
    # Drawn as explicit segments so the cycle is unmistakable.
    rx = xs["Reflector"] + W * 0.30          # exit under the Reflector
    gx = xs["Grader"] + W * 0.70             # re-enter under the Grader
    ly = 0.545                                # the loop's horizontal rail
    ax.plot([rx, rx], [Y - 0.004, ly], color=DECISION, lw=2.6, zorder=2)
    ax.plot([rx, gx], [ly, ly], color=DECISION, lw=2.6, zorder=2)
    arrow(ax, (gx, ly), (gx, Y - 0.004), color=DECISION, lw=2.6)
    label(ax, (rx + gx) / 2, ly, "REVISE — regrade with the critique")

    # ---- band 3: decision branches ------------------------------------------------
    BY, BH = 0.235, 0.20
    # refusal, centered under Router
    box(ax, 0.22, BY, 0.16, BH, "Polite refusal",
        "out-of-domain request\nor non-exam scan\n\n0 grader tokens spent\ndecision logged in steps[]",
        "#fff3e4", tc=DECISION, fs=10.5, sfs=8.2)
    arrow(ax, (0.30, Y - 0.004), (0.30, BY + BH + 0.012), color=DECISION, lw=2.2)
    label(ax, 0.30, 0.53, "REFUSE")

    # knowledge base, centered under Retriever
    box(ax, 0.375, BY, 0.235, BH, "Knowledge base",
        "Pinecone index + bundled JSON\nofficial solutions per exam question\ncourse lecture notes (semantic top-k)",
        KB, fs=11, sfs=8.2)
    arrow(ax, (0.4925, BY + BH + 0.012), (0.4925, Y - 0.004), color=KB, lw=2.2)
    ax.text(0.503, 0.53, "grounding evidence", fontsize=8.2, color=KB, ha="left", va="center")

    # human review queue, centered under Reflector
    box(ax, 0.75, BY, 0.22, BH, "Human TA review queue",
        "grader sample disagreement\nillegible after zoom re-read\nofficial solution missing/contradicted\nnever a silent guess",
        "#fdecec", tc=HUMAN, fs=10.5, sfs=8.2)
    arrow(ax, (0.93, Y - 0.004), (0.93, BY + BH + 0.012), color=HUMAN, lw=2.2)
    label(ax, 0.93, 0.53, "ESCALATE", color=HUMAN)

    # ---- band 4: assembly + result ------------------------------------------------
    OY, OH = 0.025, 0.13
    box(ax, 0.03, OY, 0.30, OH, "Orchestrator · GradeBook",
        "assembles per-question results + final report\ncompletion decided arithmetically from the manifest\nbudget ceiling stops the run — never overspends",
        DET, fs=11, sfs=8.2)
    box(ax, 0.64, OY, 0.33, OH, "Result — response + steps[] + meta",
        "score · partial credit · feedback per question\nTA-review queue · full execution trace\n(every module call, prompt, and response)",
        "#1f2937", fs=11, sfs=8.2)
    arrow(ax, (0.333, OY + OH / 2), (0.636, OY + OH / 2), lw=2.6)
    # the graded questions flow down into the GradeBook
    arrow(ax, (0.09, Y - 0.004), (0.11, OY + OH + 0.012), lw=2.0, con="arc3,rad=0.10")

    ax.text(0.03, 0.0, "aux modules (also logged): Config — active tuning snapshot at "
                       "run start · Zoom — pass-2 re-read of faint regions",
            fontsize=8.2, color=MUTED, ha="left", va="bottom")

    for out in (os.path.join(ROOT, "static", "architecture.png"),
                os.path.join(ROOT, "public", "architecture.png")):
        fig.savefig(out, facecolor=BG)
        print("wrote", out)


if __name__ == "__main__":
    main()
