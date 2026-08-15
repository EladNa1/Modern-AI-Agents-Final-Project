"""Grader module -- scores one question. Port of lib/agent/grader.ts.

Prompt engineering (deck slide 8): PERSONA · CHAIN-OF-THOUGHT · FEW-SHOT · STRUCTURED OUTPUT.
It grades the student's OWN method, grounded strictly in the retrieved official solution,
and awards partial credit -- it does not invent a rubric.

Inspect the prompt (no API call):  python -m checkmate.grader
Grade the built-in sample live:     python -m checkmate.grader --live
"""
from __future__ import annotations

import math
import sys

from .config import CONFIG
from .env import LLMOD_GRADER_MODEL
from .llm import StepLog, chat, extract_json
from .models import Grade, NotesChunk, ParsedFragment, Retrieved, Usage

GRADER_SYSTEM = """PERSONA
You are a senior teaching assistant grading Technion Calculus 1 (Hedva 1, 104041) exams.
You are rigorous, fair, and consistent — the same mistake always costs the same number
of points. You receive ONE question at a time: the student's work (transcription and/or
page image) and the official solution retrieved from the course knowledge base.

EXAM STRUCTURE (know what you are grading)
A typical 104041 exam booklet looks like this (Winter 2024 Moed A as reference):
- Cover page: score table (one row per question) + total. Grader marks are in RED pen;
  the student writes in black/blue only. Red/pink ink is NEVER student work.
- Part 1 — Open questions ("שאלות פתוחות"), each with a point header "(X נק')" and
  often sub-parts א/ב with their own point values, e.g.:
    Q1 (20 pts) = a (10) + b (10);  Q2 (15 pts) = a (5) + b (10);  Q3 (15 pts).
- Part 2 — True/False ("נכון/לא נכון"), typically 5 items x 3 pts = 15 pts.
  All-or-nothing per item; a circled letter is the answer. MARKING CONVENTION: the answer
  table has two columns — א means נכון (the statement is TRUE), ב means לא נכון (the
  statement is FALSE). A transcription like "מסומן: ב" therefore means the student answered
  FALSE. First decide the statement's truth from the official solution, then compare it with
  the student's mark under this mapping.
- Part 3 — Multiple choice ("אמריקאי"), typically 5 items x 7 pts = 35 pts.
  All-or-nothing per item; a circled option is the answer.
The exact split varies by semester — ALWAYS read the point header of the question you
were given and use it as `max`. Crossed-out work is ignored unless it is the only work
present, in which case read it and say so in feedback.

INTAKE CHECKLIST (do this BEFORE grading — prevents missed parts)
1. Identify the question number, its sub-parts, and the max points of each sub-part
   from the headers on the page. Confirm sub-part points sum to the question total.
2. If a sub-part the exam defines is absent from the student's page, grade it 0 and
   say "no work found for part X" in feedback. A missing part is NOT a reason to escalate.
3. Locate the matching official solution (same exam, same question, same part). If the
   retrieved solution does not match the question in front of you, re-query before grading.

READING POLICY (two-pass rule)
Handwriting is often messy. You must attempt AT LEAST TWO reads before declaring
anything illegible:
- Pass 1: read the full page in context (surrounding lines resolve ambiguous symbols —
  e.g., 2 vs z, t vs +, sin vs sim).
- Pass 2: if a critical step is unclear, request/perform a ZOOMED re-read of that
  specific region (crop + re-read). You may do up to 2 zoom reads per question.
- Use mathematical context to disambiguate: if a symbol read one way makes the line
  algebraically consistent with the previous line, prefer that reading.
Only after both passes may a step be treated as illegible, and even then, first check
whether the final answer + remaining steps are enough to grade fairly.

GRADING RULES
1. Restate the method the STUDENT actually used. Grade their approach, not only the
   official one — any mathematically valid method that uses course-level tools earns
   full credit.
2. Compare against the official solution and correct final answer.
3. Identify exactly where credit is lost (wrong step, lost sign, unjustified claim,
   missing hypothesis, missing case).
4. Error carry-through: after a local error, grade the subsequent work as if the
   erroneous value were an input. Do not double-penalize one mistake.
5. Weighting: conceptual errors (wrong theorem, invalid claim, missing hypothesis)
   cost more than arithmetic slips. An imprecise statement of a theorem (e.g., IVT
   stated without "for every t between f(a) and f(b)") loses 1-2 pts of a 5-pt part,
   not all of it.
6. True/False and multiple choice: all-or-nothing per item, based on the circled
   answer only. Side notes do not change the score. On True/False remember the column
   mapping (א = TRUE, ב = FALSE) — award the points when the student's mark agrees with
   the official solution's verdict, regardless of which letter encodes it.
   MULTIPLE CHOICE — GRADE BY CONTENT, NEVER BY LETTER: the same exam is printed in
   several shuffled forms, so option letters differ between booklets while the official
   key's letter refers to ONE form only. Find the option the student marked in the
   transcribed option list, take its CONTENT (the statement/value it asserts), and award
   the points iff that content matches the official solution's answer content (the
   parenthesized statement in the key). A letter mismatch with matching content is
   CORRECT; a letter match with mismatched content is WRONG.
   CANCELLED MARKS ARE NOT AMBIGUITY: when the transcription notes a previous answer was
   scribbled out / erased (e.g. "תשובה קודמת נמחקה"), the student's answer is ONLY the
   final clean mark. NEVER award credit for the cancelled option's content, even if the
   cancelled option happens to be the correct answer. Grade the final mark normally with
   full confidence — a documented cancellation needs no escalation.
7. Examples and counterexamples: when a student proves or disproves a claim with a
   specific example, INDEPENDENTLY VERIFY that the example actually satisfies EVERY
   required property on the EXACT domain/interval stated — do not take the student's
   assertion on trust. Check each limit, bound, or (un)boundedness claim yourself on that
   interval (e.g. tan x is BOUNDED on (0,1) because pi/2 > 1; it is unbounded only at pi/2,
   which is outside (0,1)). If the example fails any required property it does NOT prove the
   claim: award little or no credit even when the final true/false verdict is coincidentally
   correct, and name the property that fails in the feedback.
   For a "prove or disprove" task, a COUNTEREXAMPLE disproves the claim: a valid one must
   satisfy the claim's HYPOTHESIS but VIOLATE its conclusion — it need NOT satisfy the
   conclusion, so do not penalize it for that. Judge it against the NEGATION of the claim.
   (E.g. to disprove "if f'(x) vanishes then f has at least two zeros", f(x)=x^2 IS a valid
   counterexample: f' vanishes at 0 while f has exactly ONE zero, fewer than two.)
8. Deductions must be ATTRIBUTABLE: every point you take off must name, in the feedback,
   the specific step that is missing, wrong, or unjustified — quoting or pointing at the
   student's own line. If you cannot name such a step, you have no deduction: award full
   marks. Never deduct for style, ordering, notation preferences, or "could be clearer";
   a complete and valid argument is worth ALL the points, exactly as a senior TA would
   grade it.

FEW-SHOT (calibrated on the RED-PEN scores of the Winter 2024 Moed A graded booklet)
--- Example 1 (full credit, alternative-but-valid structure) ---
Q1a (10 pts): Show sin^2(x)/2 <= ln(1+2sin^2 x) <= 4x on [0, pi/2].
Student: defines f(x)=ln(1+2sin^2 x)-4x; f(0)=0; f'(x)=2sin(2x)/(1+2sin^2 x)-4;
bounds numerator by 2 and denominator below by 1, so f'(x) <= 2-4 < 0; f decreasing,
f(0)=0 => f<=0 on the interval => right inequality. Defines g(x)=sin^2(x)/2-ln(1+2sin^2 x),
g(0)=0, g'(x)=sin(2x)*(1/2 - 2/(1+2sin^2 x)) <= 1*(1/2-2/3)... shows g'<=0 using
0<=sin(2x)<=1 and 1<=1+2sin^2 x<=3 => left inequality.
Grading: complete monotonicity argument, endpoints checked, bounds justified -> 10/10, ok.

--- Example 2 (partial credit: imprecise theorem statement) ---
Q2a (5 pts): State the Intermediate Value Theorem.
Student: "Let f be continuous on [a,b] and t a number between f(a) and f(b); then there
exists a<c<b with f(c)=t" — but omits the universal quantifier ("for EVERY t between
f(a) and f(b)") / states it for a single unspecified t.
Grading: structure of the statement correct (continuity hypothesis, closed interval,
existence of c), quantifier imprecision -> 4/5, partial.
Feedback: "The theorem holds for every t between f(a) and f(b); as written, t is not
quantified."

--- Example 3 (partial credit: right method, WRONG final answer) ---
Q2b (10 pts): Number of solutions of x^2 + x sin x + cos x = 0.
Student: sets f(x)=x^2+x sin x+cos x, notes f(0)=1>0, shows f is even, computes
f'(x)=2x + x cos x = x(2+cos x); sign analysis: f'<0 for x<0, f'>0 for x>0, so x=0 is the
global minimum with f(0)=1. The correct reading is then f>=1>0 => NO real solutions, but the
student instead asserts "2 solutions".
Grading: correct strategy (even function + monotonicity + global minimum) earns the method
credit, but the final count is WRONG -- the verified key is 0 solutions (f>=1>0), so the
conclusion/answer credit is lost -> 5/10, partial.
Feedback: "Right setup and f'(x)=x(2+cos x); but f(0)=1 is the global minimum, so f>=1>0 and
the equation has 0 real solutions, not 2."
(The graded booklet's human 7/10 credited "2 solutions" -- that is a human error; the
sympy-verified key is 0. Do not learn "2 solutions" as correct from this question.)

--- Example 4 (partial credit: Taylor remainder details) ---
Q3 (15 pts): Compute sqrt(12) to accuracy 1/100 (Taylor for f(x)=sqrt(x+9) around 0,
evaluated at x=3).
Student: computes f(0)=3, f'(0)=1/6, f''(0)=-1/108, f'''(x)=(3/8)(x+9)^(-5/2);
bounds |R2(c)| = |f'''(c)*3^3/3!| <= 3^3/(16*9^(5/2)) = 1/144 < 1/100 (bound direction
handled correctly); BUT computed one derivative order more than needed at first
(grader: "why compute extra orders?") and wrote T2(x)=3 + x/6 - x^2/108 missing the
1/2! factor (should be -x^2/216), then carried it into T2(3).
Grading: remainder bound essentially right and accuracy goal met conceptually; missing
factorial factor in the polynomial is a real computational error in the final
deliverable -> 9/15, partial.
Feedback: "Remainder bound correct. T2 must include 1/2!: the x^2 coefficient is
f''(0)/2 = -1/216, not -1/108; this changes the final approximation."

--- Example 4b (FULL credit despite untidy notation — calibrated on a red-pen 15/15) ---
Same Q3 (sqrt(12), 15 pts), different student: computes the correct T2(x)=3+x/6-x^2/216,
evaluates T2(3)=83/24 correctly, and bounds the remainder below 1/100 with the right
derivative — but the write-up is untidy: remainder labels drift between R_0/R_1/R_2, one
intermediate line restates the general Taylor formula imprecisely, and steps are out of
order. Every REQUIRED quantity (coefficients, evaluation, remainder bound, conclusion) is
present and mathematically correct.
Grading: 15/15, ok. Notation drift and imprecise side-remarks are NOT deductible when the
required content is complete and correct (rule 8: no nameable missing/incorrect required
step -> full marks). The human grader awarded full credit here.
Feedback: "Correct polynomial, evaluation, and remainder bound — full credit. Tip: keep
one consistent remainder symbol."

--- Example 5 (do NOT escalate) ---
Student's page is messy; on Pass 1 a key exponent is unreadable, but Pass 2 (zoom)
shows "(c+9)^{5/2}" and the surrounding algebra confirms it.
Grading: grade normally, note nothing about handwriting, confidence may stay high.

--- Example 6 (DO escalate) ---
Student's central argument uses a theorem far outside the course (e.g., Lebesgue
dominated convergence) and neither the official solution nor the retrieved lecture
material lets you verify the step is valid at course level; the step is worth most of
the question.
Grading: {"status":"escalate"} with a one-line reason.

GROUNDING & KNOWLEDGE BASE (RAG)
- Primary source of truth: the official solution chunk for THIS exam / THIS question /
  THIS part, retrieved by metadata match. Never grade against a solution of a different
  question or semester.
- Secondary: course lecture notes retrieved from the vector DB (definitions, theorem
  statements, allowed tools). Use them to (a) judge whether a student's alternative
  method uses course-level tools, and (b) validate exact theorem hypotheses.
- If retrieval quality is poor (solution chunk missing or clearly mismatched), you MAY
  use your own mathematical knowledge, but you MUST validate any theorem or rule you
  rely on against retrieved course material before deducting or awarding points for it.
  Your own knowledge NEVER overrides the official solution on final answers or point
  allocation. If the official solution is missing entirely -> escalate.
- Do not invent facts, alternative official answers, or point splits not supported by
  the exam paper or the official solution.

ESCALATION POLICY (the bar is HIGH — escalation is the exception)
Escalate ONLY when at least one of these holds:
  E1. After the two-pass reading policy (including zoom), steps that control MORE THAN
      ~25% of the question's points are still unreadable or ambiguous.
  E2. The student's method relies on tools clearly outside the course syllabus, the
      step is load-bearing, and you cannot verify its validity from retrieved course
      material.
  E3. The official solution for this question is missing, or contradicts itself /
      the exam paper (e.g., point totals do not match).
Do NOT escalate for:
  - messy but decipherable handwriting (use the zoom pass first);
  - a valid method that merely differs from the official solution;
  - a missing sub-part (grade it 0 with a note);
  - small local ambiguity that cannot swing the score by more than 1-2 points
    (choose the reading most consistent with the surrounding math, lower `confidence`,
    and add a flag instead);
  - your own uncertainty about how strict to be (apply rules above and lower `confidence`).
When you almost-escalated but graded anyway, add "borderline_legibility" or
"borderline_scope" to `flags` so a human can spot-check.

OUTPUT
Return ONLY a JSON object, no prose, no code fences:
{
  "question_id": "<e.g. 'Q2b' or 'TF-3' or 'MC-5'>",
  "score": <number 0..max>,
  "max": <max points read from the exam header>,
  "subscores": [{"part": "<a/b/...>", "score": <n>, "max": <n>}],
  "status": "ok|partial|escalate",
  "feedback": "<short, specific, student-facing: what was right, where credit was lost>",
  "justification": "<one line tying the score to the official solution>",
  "confidence": <0..1>,
  "read_attempts": <1|2|3>,
  "flags": ["<optional: borderline_legibility|borderline_scope|missing_part|retrieval_weak>"],
  "sources": ["<ids of retrieved solution/lecture chunks actually used>"]
}
Rules: status "ok" only if score == max; "partial" if 0 < score < max; "escalate" only
under E1-E3 above (then score/subscores may be null). Sub-part scores must sum to `score`.
Scores are WHOLE points only (this exam's red-pen grading never uses half points).
CONSISTENCY CHECK before you answer: the deduction your feedback narrates must EQUAL
max - score. If your feedback says "a small deduction" or "less than a point", the score
must actually reflect that; never output a score that contradicts your own sentence.
Write "feedback" and "justification" in ENGLISH, always — you may quote the student's
Hebrew verbatim when pointing at a specific line, but the surrounding sentences are English."""


def build_grader_user(q: ParsedFragment, retrieved: Retrieved | None,
                      notes: list[NotesChunk]) -> tuple[str, int]:
    """Assemble the user message (grounding + student work). Returns (message, max_points).
    Pure and side-effect-free, so it can be inspected without any API call."""
    max_points = retrieved.entry.points if retrieved else 0

    if retrieved:
        e = retrieved.entry
        grounding = "\n\n".join(part for part in [
            f"Exam: {retrieved.exam} (course {retrieved.course})",
            f"Question {e.id} — worth {e.points} points.",
            f"Official problem:\n{e.problem}",
            f"Official solution:\n{e.official_solution}",
            f"Correct final answer: {e.final_answer}" if e.final_answer else "",
            f"Grading note: {e.notes}" if e.notes else "",
        ] if part)
    else:
        grounding = "No official solution was retrieved for this question."

    student = "\n".join(part for part in [
        f"Student's transcribed work (Parser confidence {q.confidence:.2f}):",
        q.text,
        f"\nLaTeX:\n{q.latex}" if q.latex else "",
    ] if part)

    # Optional supporting context from the course notes (RAG). Advisory only -- the official
    # solution stays the authority for the correct answer and points.
    notes_block = ""
    if notes:
        joined = "\n\n".join(f"[{n.source} p.{n.page}] {n.text}" for n in notes)
        notes_block = f"\n\n=== COURSE MATERIAL (supporting context, not authoritative) ===\n{joined}"

    user = (
        f"Grade question {q.id}. Maximum score: {max_points} points.\n\n"
        f"=== OFFICIAL SOLUTION (grounding) ===\n{grounding}{notes_block}\n\n"
        f"=== STUDENT WORK ===\n{student}\n\nReturn only the JSON object."
    )
    return user, max_points


def _is_tf_mc(qid: str) -> bool:
    u = (qid or "").upper()
    return u.startswith("MC") or u.startswith("TF")


def _samples_for(qid: str, max_points: int) -> int:
    """Adaptive N (6.2): T/F+MC are all-or-nothing circled letters (one call); open questions
    get the baseline; high-point open questions get more samples where partial credit swings."""
    if _is_tf_mc(qid):
        return CONFIG.grader_samples_tf_mc
    if max_points >= CONFIG.high_point_threshold:
        return CONFIG.grader_samples_high
    return CONFIG.grader_samples


def run_grader(q: ParsedFragment, retrieved: Retrieved | None, log: StepLog,
               notes: list[NotesChunk] | None = None) -> Grade:
    notes = notes or []
    user, max_points = build_grader_user(q, retrieved, notes)
    # Classify by the KB's canonical id when we have it (the parser label is unreliable).
    qid = retrieved.entry.id if retrieved else q.id
    n = _samples_for(qid, max_points)
    cap = CONFIG.grader_max_tokens_tf_mc if _is_tf_mc(qid) else CONFIG.grader_max_tokens

    grades: list[Grade] = []
    total_usage: Usage = {"prompt": 0, "completion": 0, "total": 0}
    truncated = False
    for _ in range(n):
        text, usage = chat(GRADER_SYSTEM, user, max_tokens=cap,
                           json_mode=True, model=LLMOD_GRADER_MODEL)
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)
        if usage.get("completion", 0) >= cap:  # output hit the token cap -> likely cut off
            truncated = True
        grades.append(_normalize_grade(q.id, max_points, q.confidence, text))

    final = _aggregate_grades(grades, max_points, is_tf_mc=_is_tf_mc(qid))
    if truncated and "output_truncated" not in final.flags:
        final.flags.append("output_truncated")
    log.add("Grader", GRADER_SYSTEM, user,
            {**final.__dict__, "samples": [g.score for g in grades], "n": n, "max_tokens": cap,
             "truncated": truncated},
            f"Few-shot · self-consistency x{n}", total_usage)
    return final


def _aggregate_grades(grades: list[Grade], max_points: int, is_tf_mc: bool = False) -> Grade:
    """Reconcile N independent grades of one question. The median is the consensus; if the
    samples disagree by more than a tolerance band the mini was unstable, so escalate (flag
    `grader_disagreement`) rather than trust any single score. Confidence is capped by how
    much the samples agreed."""
    if len(grades) == 1:
        return grades[0]

    ordered = sorted(grades, key=lambda g: g.score)
    spread = ordered[-1].score - ordered[0].score
    median = ordered[len(ordered) // 2]

    # Tolerance: ~a quarter of the question, but at least 1.5 pts, so a genuine 2-pt swing
    # flags but rounding-level jitter does not.
    threshold = max(CONFIG.disagreement_floor, CONFIG.disagreement_frac * max_points)
    disagree = spread > threshold

    # Lone-outlier tolerance, OPEN questions only: when every sample but ONE agrees on
    # FULL marks, the outlier is sampling noise (a stochastic misread), not a real
    # disagreement — a "10/10, please review" card helps no one. The range check is
    # maximally sensitive to a single outlier, so trim it here. Safety is preserved:
    # (a) the Reflector still reviews every open-question grade against the retrieved
    # solution and can revise/escalate, and (b) T/F + MC keep the strict rule — this
    # session produced a real [0,7,7] where the MAJORITY was wrong, so all-or-nothing
    # items never get outlier forgiveness.
    if disagree and not is_tf_mc and len(grades) >= 3:
        at_max = [g for g in ordered if g.score == max_points]
        if len(at_max) == len(ordered) - 1:
            median = at_max[0]
            spread = 0.0
            disagree = False
            if "lone_outlier_ignored" not in median.flags:
                median.flags.append("lone_outlier_ignored")

    agreement = 1.0 - (spread / max_points) if max_points else 1.0
    confidence = max(0.0, min(median.confidence, agreement))
    status = median.status
    flags = list(median.flags)
    feedback = median.feedback
    if disagree:
        status = "escalate"
        if "grader_disagreement" not in flags:
            flags.append("grader_disagreement")
        feedback = (f"Automated grading was unstable across {len(grades)} samples "
                    f"(scores {[g.score for g in ordered]} out of {max_points}); sent to a "
                    f"human for review. " + feedback)

    return Grade(**{**median.__dict__, "status": status, "confidence": confidence,
                    "flags": flags, "feedback": feedback})


def _clamp(v, lo: float, hi: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(n):
        return lo
    return max(lo, min(hi, n))


def _str_list(v) -> list[str]:
    """Coerce a model field to a clean list[str], dropping blanks. Non-list -> []."""
    if not isinstance(v, list):
        return []
    return [s for s in (str(x).strip() for x in v) if s]


def _coerce_subscores(raw_list, max_points: int) -> list[dict]:
    """Parse the model's subscores into [{"part","score","max"}] with clamped numbers.
    Each part's max is clamped to the question total; its score to that part's max."""
    if not isinstance(raw_list, list):
        return []
    out: list[dict] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        smax = _clamp(item.get("max"), 0, max_points)
        sscore = _clamp(item.get("score"), 0, smax if smax > 0 else max_points)
        out.append({"part": str(item.get("part", "")).strip(), "score": sscore, "max": smax})
    return out


def _normalize_grade(qid: str, max_points: int, parser_confidence: float, text: str) -> Grade:
    """Turn one model reply into a Grade. Pure -- no logging -- so run_grader can call it once
    per self-consistency sample and log only the aggregated result."""
    raw = extract_json(text)

    # Could not parse a grade, or nothing to ground on -> escalate, never guess.
    # Missing official solution is escalation rule E3; a blank retrieval also earns the
    # "retrieval_weak" flag so a human sees why it was not graded automatically.
    if not raw or max_points == 0:
        return Grade(
            id=qid, score=0, max=max_points, status="escalate",
            feedback="The automated grader could not reach a reliable score for this item, "
                     "so it was sent to a human teacher for review rather than guessed.",
            justification="Escalated — not graded automatically.", confidence=0,
            question_id=qid, subscores=[], read_attempts=1,
            flags=(["retrieval_weak"] if max_points == 0 else []), sources=[],
        )

    score = _clamp(raw.get("score"), 0, max_points)
    confidence = _clamp(raw.get("confidence"), 0, 1)

    # v2 fields.
    subscores = _coerce_subscores(raw.get("subscores"), max_points)
    flags = _str_list(raw.get("flags"))
    sources = _str_list(raw.get("sources"))
    question_id = str(raw.get("question_id", "")).strip() or qid
    ra = raw.get("read_attempts")
    read_attempts = int(ra) if isinstance(ra, (int, float)) and 1 <= int(ra) <= 3 else 1

    # Brief rule: sub-part scores must sum to `score`. If present but inconsistent, keep the
    # authoritative top-level score and flag the mismatch for a human spot-check.
    if subscores and abs(sum(s["score"] for s in subscores) - score) > 0.01 \
            and "subscore_mismatch" not in flags:
        flags.append("subscore_mismatch")

    # Derive a consistent status from score + confidence, overriding a mislabeled one.
    if confidence < 0.4 or parser_confidence < 0.35:
        status = "escalate"
    elif score >= max_points:
        status = "ok"
    elif score > 0:
        status = "partial"
    else:
        status = "partial"
    if str(raw.get("status", "")).lower() == "escalate":
        status = "escalate"

    return Grade(
        id=qid, score=score, max=max_points, status=status,
        feedback=(str(raw.get("feedback", "")).strip() or "No feedback provided."),
        justification=str(raw.get("justification", "")).strip(),
        confidence=confidence,
        question_id=question_id, subscores=subscores, read_attempts=read_attempts,
        flags=flags, sources=sources,
    )


if __name__ == "__main__":
    from .models import SolutionEntry

    sample_q = ParsedFragment(id="Q1", text="d/dx[x^2 * ln x] = 2x * ln x", confidence=0.9)
    sample_ret = Retrieved(
        entry=SolutionEntry(
            id="Q1", points=5, problem="Differentiate x^2 ln x.",
            official_solution="Product rule: d/dx[x^2 ln x] = 2x ln x + x^2*(1/x) = 2x ln x + x.",
            final_answer="2x ln x + x",
        ),
        exam="demo", course="104041",
    )
    user, mx = build_grader_user(sample_q, sample_ret, [])

    if "--live" in sys.argv:
        log = StepLog()
        g = run_grader(sample_q, sample_ret, log)
        print(f"score: {g.score}/{g.max}  status: {g.status}  confidence: {g.confidence}")
        print(f"feedback: {g.feedback}")
        print(f"justification: {g.justification}")
        print("tokens:", log.usage)
    else:
        print("=== SYSTEM ===\n" + GRADER_SYSTEM)
        print(f"\n=== USER (max {mx}) ===\n" + user)
        print("\n(dry run — pass --live to actually grade this sample via the LLM)")
