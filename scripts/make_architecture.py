# Generates public/architecture.png — CheckMate agent architecture.
# Reflection Agent, matching the Demo Day deck (CheckMate.pptx slide 7).
# Module names MUST stay consistent with the steps[] trace (lib/agent/*, lib/mockAgent.ts),
# agent_info, and the UI: Parser, Retriever, Grader, Reflector (+ Knowledge base).
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

NAVY = "#1F2430"; INDIGO = "#534AB7"; GREEN = "#1D8F73"; AMBER = "#BA7517"
MUTED = "#64708A"; TINT = "#F3F4FB"; TINT2 = "#E4E6F2"; WHITE = "#FFFFFF"

fig, ax = plt.subplots(figsize=(12, 6.75), dpi=110)
fig.patch.set_facecolor(WHITE)
ax.set_xlim(0, 120); ax.set_ylim(0, 67.5); ax.axis("off")

def box(x, y, w, h, title, sub, fc, ec, tc=WHITE, subc=None, rad=2.2, fs=13, subfs=9.0):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle=f"round,pad=0.2,rounding_size={rad}",
        linewidth=1.6, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(x + w/2, y + h/2 + (2.0 if sub else 0), title,
            ha="center", va="center", fontsize=fs, fontweight="bold", color=tc, zorder=4)
    if sub:
        ax.text(x + w/2, y + h/2 - 2.6, sub, ha="center", va="center",
                fontsize=subfs, color=subc or tc, zorder=4)

def arrow(x1, y1, x2, y2, color=MUTED, style="-|>", lw=2.0, ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
        arrowstyle=style, mutation_scale=16, linewidth=lw,
        color=color, linestyle=ls, zorder=2, connectionstyle=f"arc3,rad={rad}"))

# Title
ax.text(3, 63.6, "CheckMate — agent architecture", fontsize=19,
        fontweight="bold", color=NAVY, family="serif")
ax.text(3, 59.7, "Reflection Agent · grade, then self-critique against retrieved course evidence · "
        "every LLM call on gpt-5.4-mini · escalates when unsure.",
        fontsize=10.5, color=MUTED)

# Knowledge base + Retriever (RAG) — above the main flow
box(30, 51, 24, 7.5, "Knowledge base", "course notes · official solutions · TA examples",
    TINT, MUTED, tc=NAVY, subc=MUTED, rad=1.6, fs=11.5, subfs=8.5)
box(60, 51, 18, 7.5, "Retriever", "RAG match", INDIGO, "#3F378F", subc="#D8D4F2",
    rad=1.6, fs=12, subfs=9)
arrow(54, 54.75, 60, 54.75, color=MUTED, lw=1.6)

# Main pipeline row
row_y, bh = 34, 11
box(3, row_y, 15, bh, "Scanned exam", "PDF / img", TINT2, MUTED, tc=NAVY, subc=MUTED)
box(22, row_y, 17, bh, "Parser", "vision OCR · split", INDIGO, "#3F378F", subc="#D8D4F2")
box(48, row_y, 17, bh, "Grader", "score + feedback", INDIGO, "#3F378F", subc="#D8D4F2")
box(74, row_y, 17, bh, "Reflector", "self-critique", INDIGO, "#3F378F", subc="#D8D4F2")
box(96, row_y, 21, bh, "Result", "grade + feedback + steps[]", "#DFF3EE", GREEN,
    tc=NAVY, subc=GREEN, subfs=8.2)

# Flow arrows
arrow(18, row_y + bh/2, 22, row_y + bh/2)
arrow(39, row_y + bh/2, 48, row_y + bh/2)
arrow(65, row_y + bh/2, 74, row_y + bh/2)
arrow(91, row_y + bh/2, 96, row_y + bh/2)
ax.text(93.5, row_y + bh + 1.2, "approve", fontsize=8.5, color=GREEN, ha="center", style="italic")

# Retriever feeds Grader and Reflector (RAG evidence, dashed)
arrow(64, 51, 56.5, row_y + bh, color=INDIGO, lw=1.5, ls="--")
arrow(70, 51, 80, row_y + bh, color=INDIGO, lw=1.5, ls="--")
ax.text(66, 47.5, "RAG evidence", fontsize=8.2, color=INDIGO, ha="center", style="italic")

# Reflector revise loop (grade -> reflect -> revise, max N)
arrow(78, row_y, 70, row_y, color=AMBER, ls="--", rad=-0.55)
ax.text(70.5, 27.6, "revise ≤ N passes", fontsize=8.5, color=AMBER, ha="center", style="italic")

# Escalate -> teacher
box(70, 15, 25, 7, "Escalate → teacher", "if still unsure after N", AMBER, "#8F5A11",
    subc="#F6E3C4", rad=1.6, fs=11, subfs=8.2)
arrow(82.5, row_y, 82.5, 22, color=AMBER, ls="--")

# Legend
lx = 3
for label, col, ls in [("LLM (gpt-5.4-mini)", INDIGO, "-"),
                       ("RAG evidence", INDIGO, "--"),
                       ("Escalate", AMBER, "--")]:
    ax.add_patch(FancyBboxPatch((lx, 5.5), 2.4, 2.4, boxstyle="round,pad=0.1,rounding_size=0.6",
        facecolor=col, edgecolor="none", zorder=3))
    ax.text(lx + 3.2, 6.7, label, fontsize=9, color=NAVY, va="center")
    lx += 8 + len(label) * 1.35

out = os.path.join(os.path.dirname(__file__), "..", "public", "architecture.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=WHITE, pad_inches=0.25)
print("wrote", os.path.abspath(out))
