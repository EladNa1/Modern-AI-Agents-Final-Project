"""
Deterministic exam-structure extractor (Python port of lib/kb/examStructure.ts).

Question numbers and point values are the one part of a knowledge-base entry that must be
exact -- they decide the score a student receives. Reading them with a vision call proved
unreliable (the model runs at temperature 1 and invents numbers on continuation pages, and
turns multiple-choice OPTIONS into sub-questions), so we read them from the PDF text layer
instead, where they are printed literally.

Two PDF shapes are handled:
  1. Older Technion finals: Hebrew in VISUAL (RTL-reordered) order, one question per line
     with the points printed inline -- the primary pass below.
  2. Newer finals (e.g. 104018 2025s, 104041 2026w): Hebrew in real LOGICAL order but each
     printed line split into many fragments, and question numbering restarts in the
     multiple-choice ("אמריקאי") section -- the fallback pass, used only when the primary
     finds nothing.

The vision pass still writes the prose (problem statement, worked solution); this module
only supplies the skeleton it must fill.

Run (verify parity with the TS version, no API calls):
    python lib/kb/exam_structure.py "<some exam sol.pdf>" ["<another.pdf>" ...]
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import fitz  # pymupdf -- same MuPDF engine the TS side uses via WASM


@dataclass
class StructQuestion:
    id: str            # "Q6a" -- canonical, matches the KB entry id
    number: int        # 6
    points: int
    section: str       # "חלק ב'"
    page: int          # 1-based page the question starts on
    part: str | None = None  # "a"


@dataclass
class ExamStructure:
    questions: list[StructQuestion]
    total: int
    sections: list[dict]
    # Which question ids each 1-based page shows. A page with no marker of its own inherits
    # the question still in progress, so a vision pass over it knows what it is looking at.
    ids_by_page: dict[int, list[str]] = field(default_factory=dict)


HE_PARTS = {"א": "a", "ב": "b", "ג": "c", "ד": "d", "ה": "e"}

# "חלק א'" -- a section heading.
RE_SECTION = re.compile(r"חלק\s*([אבגדה])")
# "משקל כל שאלה בחלק זה הוא 8 נקודות" -- one weight for every question in the section.
RE_SECTION_WEIGHT = re.compile(r"משקל\s*כל\s*שאלה")
# A numbered question: the number lands at the end of the visual line (".6", ".1").
RE_QUESTION = re.compile(r"\.(\d+)\s*$")
# A sub-part or an answer option. Newer PDFs emit "()א"; older ones read "(א)".
RE_PART = re.compile(r"\(\)\s*([אבגדה])|\(\s*([אבגדה])\s*\)")
# A printed point value, scattered three ways by visual reordering.
RE_POINTS = [
    re.compile(r"\(\s*נקודות\s*(\d+)\s*\)"),   # "(נקודות10)"
    re.compile(r"\(\s*(\d+)\s*נקודות\s*\)"),   # "(10 נקודות)"
    re.compile(r"נקודות\s*\([^)]*?(\d+)\s*\)"),  # "נקודות( …text… 10)"
]


def points_on(line: str) -> int:
    for rex in RE_POINTS:
        m = rex.search(line)
        if m:
            return int(m.group(1) or 0)
    return 0


def decode_legacy_hebrew(s: str) -> str:
    """Older PDFs embed Hebrew in a legacy cp1255 font -> Latin-1 gibberish. The mapping is
    a fixed offset and the text is visual-order, so decode and reverse each Hebrew run."""
    decoded = "".join(
        chr(0x05D0 + (ord(ch) - 0xE0)) if 0xE0 <= ord(ch) <= 0xFA else ch
        for ch in s
    )
    return re.sub(r"[א-ת]+", lambda m: m.group(0)[::-1], decoded)


def needs_legacy_decode(pages: list[list[str]]) -> bool:
    """Real Hebrew already present means the text layer is fine and must not be touched."""
    real = legacy = 0
    for lines in pages:
        for line in lines:
            for ch in line:
                c = ord(ch)
                if 0x05D0 <= c <= 0x05EA:
                    real += 1
                elif 0xE0 <= c <= 0xFA:
                    legacy += 1
    return real == 0 and legacy > 20


def pdf_lines_by_page(pdf: str | bytes) -> list[list[str]]:
    """Pull the text lines out of a PDF, grouped per page, in reading order."""
    doc = fitz.open(stream=pdf, filetype="pdf") if isinstance(pdf, (bytes, bytearray)) else fitz.open(pdf)
    try:
        pages: list[list[str]] = []
        for page in doc:
            d = page.get_text("dict")
            lines: list[str] = []
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(span.get("text", "") for span in line.get("spans", []))
                    if text:
                        lines.append(text)
            pages.append(lines)
        return pages
    finally:
        doc.close()


def extract_exam_structure(pdf: str | bytes) -> ExamStructure:
    """Read the exam skeleton from the PDF text layer. Returns an empty structure when the
    PDF has no usable text layer (a pure scan), so the caller falls back to the vision pass."""
    pages = pdf_lines_by_page(pdf)
    if needs_legacy_decode(pages):
        pages = [[decode_legacy_hebrew(line) for line in lines] for lines in pages]

    sections: list[dict] = []
    questions: list[StructQuestion] = []
    ids_by_page: dict[int, list[str]] = {}

    def note_on_page(qid: str, page: int) -> None:
        ids = ids_by_page.setdefault(page, [])
        if qid not in ids:
            ids.append(qid)

    section = ""
    section_weight = 0
    current_number = 0
    header_points = 0   # value on the question header ("6. (30 נקודות)"); counts only if no scored sub-parts
    parts_seen = 0
    bare_question_page = 0

    def flush_bare_question() -> None:
        # A question that ended with no scored sub-part scores on its own: its header value,
        # or its section's per-question weight.
        if not current_number or parts_seen > 0:
            return
        pts = header_points or section_weight
        if pts > 0:
            qid = f"Q{current_number}"
            questions.append(StructQuestion(id=qid, number=current_number, points=pts, section=section, page=bare_question_page))
            note_on_page(qid, bare_question_page)

    # Flatten to (line, page) in reading order.
    flat: list[tuple[str, int]] = [(line, i + 1) for i, lines in enumerate(pages) for line in lines]

    for line, page_no in flat:
        sec = RE_SECTION.search(line)
        if sec:
            flush_bare_question()
            current_number = 0
            header_points = 0
            parts_seen = 0
            section = f"חלק {sec.group(1)}'"
            section_weight = 0  # a new section does not inherit the previous weight
            sections.append({"label": section, "pointsPerQuestion": 0})
            continue

        if RE_SECTION_WEIGHT.search(line):
            m = re.search(r"(\d+)", line)
            w = int(m.group(1)) if m else 0
            if w > 0:
                section_weight = w
                if sections:
                    sections[-1]["pointsPerQuestion"] = w
            continue

        qm = RE_QUESTION.search(line)
        if qm:
            flush_bare_question()
            current_number = int(qm.group(1))
            header_points = points_on(line)
            parts_seen = 0
            bare_question_page = page_no
            continue

        pm = RE_PART.search(line)
        if pm and current_number:
            letter = pm.group(1) or pm.group(2)
            pts = points_on(line)
            # No printed value means this letter is an ANSWER OPTION, not a sub-part to solve.
            if pts > 0:
                parts_seen += 1
                qid = f"Q{current_number}{HE_PARTS[letter]}"
                questions.append(StructQuestion(id=qid, number=current_number, part=HE_PARTS[letter], points=pts, section=section, page=page_no))
                note_on_page(qid, page_no)

    flush_bare_question()

    # The newer logical-order shape yields nothing above; fall back to it, but only if it finds something.
    if not questions:
        alt = _extract_logical_structure(pages)
        if alt.questions:
            return alt

    # A question's work often spills onto the next page with no marker reprinted.
    for p in range(1, len(pages) + 1):
        if p in ids_by_page or not pages[p - 1]:
            continue
        carried = [q for q in questions if q.page < p]
        if carried:
            ids_by_page[p] = [carried[-1].id]

    total = sum(q.points for q in questions)
    return ExamStructure(questions=questions, total=total, sections=sections, ids_by_page=ids_by_page)


def _extract_logical_structure(pages: list[list[str]]) -> ExamStructure:
    """Newer Technion PDFs keep Hebrew in real logical order but split every printed line
    into fragments, and restart numbering in the multiple-choice ("אמריקאי") section. A
    header reads " שאלה1" on its own line, its total a line or two below, and a scored
    sub-part is a bare letter line "א ." followed by its point value; in the multiple-choice
    section the same letters are answer OPTIONS and score nothing.

    Multiple-choice questions are renumbered continuously after the open ones (open Q1..Qk,
    then MC Q(k+1)..), so the whole exam stays a single Q<n> namespace and every id is unique.
    """
    questions: list[StructQuestion] = []
    ids_by_page: dict[int, list[str]] = {}

    def note_on_page(qid: str, page: int) -> None:
        ids = ids_by_page.setdefault(page, [])
        if qid not in ids:
            ids.append(qid)

    flat: list[tuple[str, int]] = [(line, i + 1) for i, lines in enumerate(pages) for line in lines]

    re_q = re.compile(r"ש\s*אלה\s*(\d+)")          # "שאלה1", "ש אלה3"
    re_mc = re.compile(r"אמריקאי\s*(\d+)")          # "אמריקאי35" (digit tells it apart from the word "אמריקאיות")
    re_letter = re.compile(r"^\s*([אבגדה])\s*\.\s*$")  # "א .", "ב." -- a sub-part marker alone on its line
    re_int = re.compile(r"^\s*(\d+)\s*$")           # a standalone integer line (a point value)

    def int_after(idx: int) -> int:
        # First standalone integer within the next few lines -- a total or a sub-part weight.
        for j in range(idx + 1, min(idx + 5, len(flat))):
            m = re_int.match(flat[j][0])
            if m:
                return int(m.group(1))
        return 0

    mc = False
    mc_offset = 0     # added to a multiple-choice number to continue past the open ones
    open_max = 0
    cur_number = 0
    cur_page = 0
    canonical_id = ""
    parts_seen = 0
    header_points = 0

    def flush_atomic() -> None:
        # A question with no scored sub-part (every MC question, any atomic open one) scores
        # on the value printed beside its header.
        if not cur_number or parts_seen > 0 or header_points <= 0:
            return
        questions.append(StructQuestion(
            id=canonical_id, number=cur_number, points=header_points,
            section="אמריקאי" if mc else "פתוח", page=cur_page,
        ))
        note_on_page(canonical_id, cur_page)

    for i, (line, page) in enumerate(flat):
        if not mc and re_mc.search(line):
            flush_atomic()
            mc = True
            mc_offset = open_max  # restarted numbers continue after the last open question
            cur_number = 0
            parts_seen = 0
            header_points = 0
            continue

        qm = re_q.search(line)
        if qm and len(line.strip()) <= 12:
            # length guard so "שאלה" inside prose ("בשאלה 2 ראינו…") is not read as a header
            flush_atomic()
            printed = int(qm.group(1))
            cur_number = printed
            cur_page = page
            parts_seen = 0
            header_points = int_after(i)
            canonical_id = f"Q{mc_offset + printed if mc else printed}"
            if not mc:
                open_max = max(open_max, printed)
            continue

        # Sub-parts exist only in the open section; a bare letter in the MC section is an option.
        if not mc and cur_number:
            lm = re_letter.match(line)
            if lm:
                pts = int_after(i)
                if pts > 0:
                    parts_seen += 1
                    qid = f"Q{cur_number}{HE_PARTS[lm.group(1)]}"
                    questions.append(StructQuestion(id=qid, number=cur_number, part=HE_PARTS[lm.group(1)], points=pts, section="פתוח", page=page))
                    note_on_page(qid, page)

    flush_atomic()

    # A page with no marker of its own belongs to the last question that started before it.
    for p in range(1, len(pages) + 1):
        if p in ids_by_page or not pages[p - 1]:
            continue
        carried = [q for q in questions if q.page < p]
        if carried:
            ids_by_page[p] = [carried[-1].id]

    total = sum(q.points for q in questions)
    return ExamStructure(questions=questions, total=total, sections=[], ids_by_page=ids_by_page)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        s = extract_exam_structure(path)
        name = path.replace("\\", "/").split("/")[-1]
        print(f"\n### {name}")
        print("ids:", ", ".join(f"{q.id}={q.points}" for q in s.questions) or "(none)")
        print(f"TOTAL: {s.total}" + (" ✓" if s.total == 100 else ""))
        pages = "  ".join(f"p{p}:[{','.join(ids)}]" for p, ids in sorted(s.ids_by_page.items()))
        print("ids_by_page:", pages)
