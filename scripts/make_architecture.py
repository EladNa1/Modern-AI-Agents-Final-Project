# Generates public/architecture.png — CheckMate agent architecture.
# Fixed pipeline, matching the Demo Day deck (slides 6-7).
# Module names must stay consistent with lib/mockAgent.ts and the UI.
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

def box(x, y, w, h, title, sub, fc, ec, tc=WHITE, subc=None, rad=2.2, fs=13, subfs=9.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle=f"round,pad=0.2,rounding_size={rad}",
        linewidth=1.6, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(x + w/2, y + h/2 + (2.0 if sub else 0), title,
            ha="center", va="center", fontsize=fs, fontweight="bold", color=tc, zorder=4)
    if sub:
        ax.text(x + w/2, y + h/2 - 2.6, sub, ha="center", va="center",
                fontsize=subfs, color=subc or tc, zorder=4)

def arrow(x1, y1, x2, y2, color=MUTED, style="-|>", lw=2.0, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
        arrowstyle=style, mutation_scale=16, linewidth=lw,
        color=color, linestyle=ls, zorder=2, connectionstyle="arc3,rad=0"))

# Title
ax.text(3, 63.5, "CheckMate — agent architecture", fontsize=19,
        fontweight="bold", color=NAVY, family="serif")
ax.text(3, 59.6, "Fixed pipeline · questions graded in parallel · every LLM call on gpt-5.4-mini. "
        "Reflection is conditional and can escalate to the teacher.",
        fontsize=10.5, color=MUTED)

# Main pipeline row (y center ~ 40)
row_y, bw, bh = 38, 17, 11
xs = [3, 25, 47, 69, 91]
# Input
box(xs[0], row_y, 15, bh, "Scanned exam", "PDF / Word / img", TINT2, MUTED, tc=NAVY, subc=MUTED)
# LLM stages
box(xs[1], row_y, bw, bh, "Reader", "OCR · split questions", INDIGO, "#3F378F", subc="#D8D4F2")
box(xs[2], row_y, bw, bh, "Retriever", "rubric match", INDIGO, "#3F378F", subc="#D8D4F2")
box(xs[3], row_y, bw, bh, "Grader", "score + feedback", INDIGO, "#3F378F", subc="#D8D4F2")
# Output
box(xs[4], row_y, 20, bh, "Result", "annotated exam + grade + steps[]", "#DFF3EE", GREEN,
    tc=NAVY, subc=GREEN, subfs=8.5)

# arrows along the row
arrow(18, row_y + bh/2, 25, row_y + bh/2)
arrow(25 + bw, row_y + bh/2, 47, row_y + bh/2)
arrow(47 + bw, row_y + bh/2, 69, row_y + bh/2)
arrow(69 + bw, row_y + bh/2, 91, row_y + bh/2)

# Knowledge base feeding Retriever (above)
box(43.5, 52, 24, 7, "Knowledge base", "solutions · rubric · graded examples",
    TINT, MUTED, tc=NAVY, subc=MUTED, rad=1.6, fs=11.5, subfs=9)
arrow(55.5, 52, 55.5, row_y + bh, color=MUTED, style="-|>", lw=1.6, ls="--")

# Reflector — conditional, hangs under Grader
box(58, 18, 20, 11, "Reflector", "conditional check", AMBER, "#8F5A11", subc="#F6E3C4")
arrow(77, row_y, 72, 29, color=AMBER, ls="--")           # Grader -> Reflector (conditional)
arrow(64, 29, 74, row_y, color=AMBER, ls="--")           # Reflector -> back into result flow
ax.text(80.5, 24.5, "only when a grade\nis shaky", fontsize=8.5, color=AMBER, style="italic", va="center")

# Reflector's four actions
actions = [("APPROVE", GREEN), ("REVISE ≤1", INDIGO), ("RETRY_OCR ≤1", INDIGO), ("ESCALATE → teacher", AMBER)]
ax_x = 3
for label, col in actions:
    w = 3.6 + len(label) * 1.15
    ax.add_patch(FancyBboxPatch((ax_x, 6), w, 5, boxstyle="round,pad=0.15,rounding_size=1.2",
        facecolor=WHITE, edgecolor=col, linewidth=1.5, zorder=3))
    ax.text(ax_x + w/2, 8.5, label, ha="center", va="center", fontsize=9, color=col, fontweight="bold")
    ax_x += w + 3
ax.text(3, 13.5, "Reflector picks exactly one action:", fontsize=9, color=MUTED)

# legend
lx = 78
for label, col in [("LLM (gpt-5.4-mini)", INDIGO), ("Conditional / escalate", AMBER)]:
    ax.add_patch(FancyBboxPatch((lx, 6.6), 2.4, 2.4, boxstyle="round,pad=0.1,rounding_size=0.6",
        facecolor=col, edgecolor="none", zorder=3))
    ax.text(lx + 3.2, 7.8, label, fontsize=9, color=NAVY, va="center")
    lx += 22 if "LLM" in label else 0
    if "LLM" in label:
        lx = 78; # place second on next line
        ax.text(81.2, 3.6, "Conditional / escalate", fontsize=9, color=NAVY, va="center")
        ax.add_patch(FancyBboxPatch((78, 2.4), 2.4, 2.4, boxstyle="round,pad=0.1,rounding_size=0.6",
            facecolor=AMBER, edgecolor="none", zorder=3))
        break

out = os.path.join(os.path.dirname(__file__), "..", "public", "architecture.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
fig.savefig(out, dpi=110, bbox_inches="tight", facecolor=WHITE, pad_inches=0.25)
print("wrote", os.path.abspath(out))
