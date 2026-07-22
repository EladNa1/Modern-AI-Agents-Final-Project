// Deterministic exam-structure extractor.
//
// Question numbers and point values are the one part of a knowledge-base entry that
// must be exact — they decide the score a student receives. Reading them with a vision
// call proved unreliable (the model runs at temperature 1 and invented numbers on
// continuation pages, and turned multiple-choice OPTIONS into sub-questions), so we
// read them from the PDF text layer instead, where they are printed literally.
//
// Technion exam PDFs store Hebrew as real Unicode but emit it in visual (RTL-reordered)
// order, so a line arrives looking like "(נקודות10) ()א". The digits and the markers
// survive that reordering intact, which is all this parser needs.
//
// The vision pass still writes the prose (problem statement, worked solution); this
// module only supplies the skeleton it must fill.

export type StructQuestion = {
  id: string; // "Q6a" — canonical, matches the KB entry id
  number: number; // 6
  part?: string; // "a"
  points: number;
  section: string; // "חלק ב'"
  page: number; // 1-based page the question starts on
};

export type ExamStructure = {
  questions: StructQuestion[];
  total: number;
  sections: { label: string; pointsPerQuestion: number }[];
  // Which question ids each 1-based page shows. A page with no marker of its own
  // inherits the question still in progress, so a vision pass over that page knows
  // what it is looking at instead of guessing.
  idsByPage: Map<number, string[]>;
};

const HE_PARTS: Record<string, string> = { א: "a", ב: "b", ג: "c", ד: "d", ה: "e" };

// "חלק א'" — a section heading.
const RE_SECTION = /חלק\s*([אבגדה])/;
// "משקל כל שאלה בחלק זה הוא 8 נקודות" — one weight for every question in the section.
const RE_SECTION_WEIGHT = /משקל\s*כל\s*שאלה/;
// A numbered question: the number lands at the end of the visual line (".6", ".1").
const RE_QUESTION = /\.(\d+)\s*$/;
// A sub-part or an answer option. Newer PDFs emit "()א"; older ones, once decoded and
// un-reversed, read "(א)".
const RE_PART = /\(\)\s*([אבגדה])|\(\s*([אבגדה])\s*\)/;
// A printed point value. Visual reordering scatters these three ways, and the third
// form splits the parentheses from the digits with the question text in between —
// e.g. "נקודות( הוכיחו כי10)" is a 10-point sub-part.
const RE_POINTS = [
  /\(\s*נקודות\s*(\d+)\s*\)/, // "(נקודות10)"
  /\(\s*(\d+)\s*נקודות\s*\)/, // "(10 נקודות)"
  /נקודות\s*\([^)]*?(\d+)\s*\)/, // "נקודות( …text… 10)"
];

function pointsOn(line: string): number {
  for (const re of RE_POINTS) {
    const m = re.exec(line);
    if (m) return Number(m[1]) || 0;
  }
  return 0;
}

// Older Technion exam PDFs embed Hebrew in a legacy cp1255 font, so the text layer
// comes out as Latin-1 gibberish ("ä÷éèîúîì"). The mapping is a fixed offset, and the
// text is in visual order, so decoding and reversing each Hebrew run restores it.
function decodeLegacyHebrew(s: string): string {
  const decoded = [...s]
    .map((ch) => {
      const c = ch.charCodeAt(0);
      return c >= 0xe0 && c <= 0xfa ? String.fromCharCode(0x05d0 + (c - 0xe0)) : ch;
    })
    .join("");
  return decoded.replace(/[א-ת]+/g, (m) => [...m].reverse().join(""));
}

// Real Hebrew already present means the text layer is fine and must not be touched.
function needsLegacyDecode(pages: string[][]): boolean {
  let real = 0;
  let legacy = 0;
  for (const lines of pages)
    for (const l of lines)
      for (const ch of l) {
        const c = ch.charCodeAt(0);
        if (c >= 0x05d0 && c <= 0x05ea) real++;
        else if (c >= 0xe0 && c <= 0xfa) legacy++;
      }
  return real === 0 && legacy > 20;
}

// Pull the text lines out of a PDF, grouped per page, in reading order.
async function pdfLinesByPage(pdf: Uint8Array): Promise<string[][]> {
  const mupdf = await import("mupdf");
  const doc = mupdf.Document.openDocument(pdf, "application/pdf");
  try {
    const pages: string[][] = [];
    for (let i = 0; i < doc.countPages(); i++) {
      const page = doc.loadPage(i);
      const st = JSON.parse(page.toStructuredText().asJSON());
      const lines: string[] = [];
      for (const b of st.blocks ?? [])
        for (const l of b.lines ?? []) if (l.text) lines.push(l.text as string);
      pages.push(lines);
      page.destroy();
    }
    return pages;
  } finally {
    doc.destroy();
  }
}

/**
 * Read the exam skeleton — every question, its canonical id, and its point value —
 * from the PDF's text layer. Returns an empty structure when the PDF has no usable
 * text layer (a pure scan), in which case the caller falls back to the vision pass.
 */
export async function extractExamStructure(pdf: Uint8Array): Promise<ExamStructure> {
  let pages = await pdfLinesByPage(pdf);
  if (needsLegacyDecode(pages)) {
    pages = pages.map((lines) => lines.map(decodeLegacyHebrew));
  }

  const sections: { label: string; pointsPerQuestion: number }[] = [];
  const questions: StructQuestion[] = [];
  const idsByPage = new Map<number, string[]>();

  let pageNo = 0; // 1-based, set as we walk
  let section = "";
  let sectionWeight = 0;
  let currentNumber = 0;
  // Points printed on the question header itself ("6. (30 נקודות)"). That is the total
  // for the whole question, so it only counts when the question has no scored sub-parts.
  let headerPoints = 0;
  let partsSeen = 0;

  // Record that a page shows this question, so the caller can tell a vision pass
  // exactly what is on the page instead of letting it guess.
  const noteOnPage = (id: string, page: number) => {
    const ids = idsByPage.get(page) ?? [];
    if (!ids.includes(id)) ids.push(id);
    idsByPage.set(page, ids);
  };

  let bareQuestionPage = 0;
  const flushBareQuestion = () => {
    // A question that ended without any scored sub-part scores on its own: either the
    // value printed on its header, or its section's per-question weight.
    if (!currentNumber || partsSeen > 0) return;
    const points = headerPoints || sectionWeight;
    if (points > 0) {
      const id = `Q${currentNumber}`;
      questions.push({ id, number: currentNumber, points, section, page: bareQuestionPage });
      noteOnPage(id, bareQuestionPage);
    }
  };

  const lines: string[] = [];
  const pageOfLine: number[] = [];
  pages.forEach((ls, i) => ls.forEach((l) => { lines.push(l); pageOfLine.push(i + 1); }));

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    pageNo = pageOfLine[li];
    const secMatch = RE_SECTION.exec(line);
    if (secMatch) {
      flushBareQuestion();
      currentNumber = 0;
      headerPoints = 0;
      partsSeen = 0;
      section = `חלק ${secMatch[1]}'`;
      sectionWeight = 0; // a new section does not inherit the previous weight
      sections.push({ label: section, pointsPerQuestion: 0 });
      continue;
    }

    if (RE_SECTION_WEIGHT.test(line)) {
      const w = Number(/(\d+)/.exec(line)?.[1]) || 0;
      if (w > 0) {
        sectionWeight = w;
        if (sections.length) sections[sections.length - 1].pointsPerQuestion = w;
      }
      continue;
    }

    const qMatch = RE_QUESTION.exec(line);
    if (qMatch) {
      flushBareQuestion();
      currentNumber = Number(qMatch[1]);
      headerPoints = pointsOn(line);
      partsSeen = 0;
      bareQuestionPage = pageNo;
      continue;
    }

    const partMatch = RE_PART.exec(line);
    if (partMatch && currentNumber) {
      const letter = partMatch[1] ?? partMatch[2];
      const pts = pointsOn(line);
      // No printed value means this letter is an ANSWER OPTION of a multiple-choice
      // question, not a sub-part to solve — it earns nothing on its own.
      if (pts > 0) {
        partsSeen++;
        const id = `Q${currentNumber}${HE_PARTS[letter]}`;
        questions.push({
          id,
          number: currentNumber,
          part: HE_PARTS[letter],
          points: pts,
          section,
          page: pageNo,
        });
        noteOnPage(id, pageNo);
      }
    }
  }
  flushBareQuestion();

  // A question's work often spills onto the next page with no marker reprinted. Such a
  // page belongs to the last question that started at or before it.
  for (let p = 1; p <= pages.length; p++) {
    if (idsByPage.has(p) || pages[p - 1].length === 0) continue;
    const carried = questions.filter((q) => q.page < p).pop();
    if (carried) idsByPage.set(p, [carried.id]);
  }

  // The visual-order parse above fits the older Technion PDFs. Newer ones (e.g. 104018
  // 2025s, 104041 2026w) store the text layer in logical reading order, split each line
  // into fragments, and restart numbering in the multiple-choice section — that parse
  // finds nothing in them. Fall back to the logical-order pass only when it does.
  if (questions.length === 0) {
    const alt = extractLogicalStructure(pages);
    if (alt.questions.length > 0) return alt;
  }

  const total = questions.reduce((s, q) => s + q.points, 0);
  return { questions, total, sections, idsByPage };
}

// Newer Technion exam PDFs keep Hebrew in real logical (reading) order but split every
// printed line into many text fragments, and they restart question numbering in the
// multiple-choice ("אמריקאי") section. A question header reads " שאלה1" on its own
// line, its total a line or two below, and a scored sub-part is a bare letter line
// "א ." followed a line or two later by its point value. In the multiple-choice section
// the same letters are answer OPTIONS and score nothing.
//
// To keep every id unique despite the restarted numbering, multiple-choice questions are
// renumbered continuously after the open ones (open Q1..Qk, then MC Q(k+1)..), so the
// whole exam stays a single Q<n> namespace the rest of the pipeline already expects.
function extractLogicalStructure(pages: string[][]): ExamStructure {
  const questions: StructQuestion[] = [];
  const idsByPage = new Map<number, string[]>();
  const noteOnPage = (id: string, page: number) => {
    const ids = idsByPage.get(page) ?? [];
    if (!ids.includes(id)) ids.push(id);
    idsByPage.set(page, ids);
  };

  const lines: string[] = [];
  const pageOf: number[] = [];
  pages.forEach((ls, i) => ls.forEach((l) => { lines.push(l); pageOf.push(i + 1); }));

  const RE_Q = /ש\s*אלה\s*(\d+)/; // "שאלה1", "ש אלה3"
  const RE_MC = /אמריקאי\s*(\d+)/; // "אמריקאי35" — the multiple-choice section header (digit distinguishes it from the intro word "אמריקאיות")
  const RE_LETTER = /^\s*([אבגדה])\s*\.\s*$/; // "א .", "ב." — a sub-part marker alone on its line
  const RE_INT = /^\s*(\d+)\s*$/; // a standalone integer line (a point value)

  // The first standalone integer within the next few lines — a question's total or a
  // sub-part's weight sits a line or two below its marker.
  const intAfter = (from: number): number => {
    for (let j = from + 1; j <= Math.min(from + 4, lines.length - 1); j++) {
      const m = RE_INT.exec(lines[j]);
      if (m) return Number(m[1]);
    }
    return 0;
  };

  let mc = false;
  let mcOffset = 0; // added to a multiple-choice number to continue past the open ones
  let openMax = 0;
  let curNumber = 0;
  let curPage = 0;
  let canonicalId = "";
  let partsSeen = 0;
  let headerPoints = 0;

  // A question with no scored sub-part (every multiple-choice question, and any atomic
  // open one) scores on the value printed beside its header.
  const flushAtomic = () => {
    if (!curNumber || partsSeen > 0 || headerPoints <= 0) return;
    questions.push({
      id: canonicalId, number: curNumber, points: headerPoints,
      section: mc ? "אמריקאי" : "פתוח", page: curPage,
    });
    noteOnPage(canonicalId, curPage);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const page = pageOf[i];

    if (!mc && RE_MC.test(line)) {
      flushAtomic();
      mc = true;
      mcOffset = openMax; // restarted numbers continue after the last open question
      curNumber = 0; partsSeen = 0; headerPoints = 0;
      continue;
    }

    const qm = RE_Q.exec(line);
    if (qm && line.trim().length <= 12) {
      // guard on length so "שאלה" inside prose ("בשאלה 2 ראינו…") is not a header
      flushAtomic();
      const printed = Number(qm[1]);
      curNumber = printed;
      curPage = page;
      partsSeen = 0;
      headerPoints = intAfter(i);
      canonicalId = `Q${mc ? mcOffset + printed : printed}`;
      if (!mc) openMax = Math.max(openMax, printed);
      continue;
    }

    // Sub-parts exist only in the open section; a bare letter in the multiple-choice
    // section is an answer option, worth nothing.
    if (!mc && curNumber) {
      const lm = RE_LETTER.exec(line);
      if (lm) {
        const pts = intAfter(i);
        if (pts > 0) {
          partsSeen++;
          const id = `Q${curNumber}${HE_PARTS[lm[1]]}`;
          questions.push({ id, number: curNumber, part: HE_PARTS[lm[1]], points: pts, section: "פתוח", page });
          noteOnPage(id, page);
        }
      }
    }
  }
  flushAtomic();

  // A page with no marker of its own belongs to the last question that started before it.
  for (let p = 1; p <= pages.length; p++) {
    if (idsByPage.has(p) || pages[p - 1].length === 0) continue;
    const carried = questions.filter((q) => q.page < p).pop();
    if (carried) idsByPage.set(p, [carried.id]);
  }

  const total = questions.reduce((s, q) => s + q.points, 0);
  return { questions, total, sections: [], idsByPage };
}
