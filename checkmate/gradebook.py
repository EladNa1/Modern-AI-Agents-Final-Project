"""GradeBook -- persists per-question grades for a booklet and emits a final exam report.

Section 8. Lives in the orchestrator's assembly (it already groups fragments by question and
builds results, so accumulating them is its existing job -- not a new stage). Zero model cost:
everything here is deterministic bookkeeping. The optional narrative summary is one text-only
call over the STORED feedback strings (no vision, no re-retrieval), gated behind a flag.

Key invariants:
- Completion is arithmetic from the KB manifest, never model-detected (8.3): a total is
  never reported on missing questions -- pages exhausted with unfilled slots => "incomplete"
  with the missing ids listed.
- Escalation is monotone (8.5): any escalated entry makes the booklet "provisional"; the
  report shows the auto-graded subtotal separately from the total-including-unreviewed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def entry_from_grade(grade, retrieved) -> dict:
    """One GradeBook entry from a Grade (+ the retrieved KB question, for cost/source)."""
    return {
        "score": grade.score, "max": grade.max, "status": grade.status,
        "subscores": list(grade.subscores), "feedback": grade.feedback,
        "justification": grade.justification, "confidence": grade.confidence,
        "flags": list(grade.flags), "sources": list(grade.sources),
    }


@dataclass
class GradeBook:
    booklet_id: str
    exam_id: str | None
    config_snapshot: dict
    manifest: dict | None
    entries: dict = field(default_factory=dict)
    history: list = field(default_factory=list)  # prior versions of re-graded entries
    cost: dict = field(default_factory=dict)

    def add(self, qid: str, entry: dict) -> None:
        """Append/update-only: a re-grade replaces the entry and archives the prior one.
        Escalation is monotone -- an escalated entry cannot be cleared by a later add."""
        prev = self.entries.get(qid)
        if prev is not None:
            self.history.append({"q": qid, **prev})
            if prev.get("status") == "escalate" and entry.get("status") != "escalate":
                entry = {**entry, "status": "escalate",
                         "flags": sorted(set(entry.get("flags", []) + prev.get("flags", []) + ["escalate_sticky"]))}
        self.entries[qid] = entry

    def missing(self) -> list[str]:
        if not self.manifest:
            return []
        return [q["id"] for q in self.manifest["questions"] if q["id"] not in self.entries]

    def is_complete(self) -> bool:
        return self.manifest is not None and not self.missing()

    def status(self) -> str:
        if any(e.get("status") == "escalate" for e in self.entries.values()):
            return "provisional"
        if self.manifest is None:
            return "complete_manifest_absent"
        return "complete" if self.is_complete() else "incomplete"

    def final_report(self, cover_scores: dict | None = None) -> dict:
        awarded = sum(e["score"] for e in self.entries.values())
        auto = sum(e["score"] for e in self.entries.values() if e.get("status") != "escalate")
        possible = self.manifest["expected_total"] if self.manifest \
            else sum(e["max"] for e in self.entries.values())
        missing = self.missing()
        escalated = [q for q, e in self.entries.items() if e.get("status") == "escalate"]
        # TA review list == the escalations, ordered by points at stake (8.4). Flags on
        # non-escalated entries (e.g. a completed revision) are annotations, not review
        # requests -- listing them here contradicted the "no review needed" summary.
        review = sorted(
            ([q, e] for q, e in self.entries.items() if e.get("status") == "escalate"),
            key=lambda x: -x[1]["max"])
        report = {
            "status": self.status(),
            "awarded": awarded, "possible": possible,
            "percent": round(100 * awarded / possible, 1) if possible else None,
            "auto_subtotal": auto, "total_including_unreviewed": awarded,
            "missing_questions": missing,
            "escalated": escalated,
            "ta_review": [{"q": q, "max": e["max"],
                           "reason": "escalate" + (f" ({','.join(e['flags'])})" if e.get("flags") else "")}
                          for q, e in review],
            "manifest_mismatch": bool(self.manifest and possible != self.manifest["expected_total"]),
            "cost": self.cost,
            "narrative": None,  # 8.4 narrative is a separate, flag-gated text-only call
        }
        if cover_scores:  # model-vs-human delta if the cover score table was read
            report["vs_human"] = [
                {"q": q, "human": cover_scores.get(q), "model": e["score"],
                 "delta": (e["score"] - cover_scores[q]) if q in cover_scores else None}
                for q, e in self.entries.items()]
        return report

    def to_dict(self) -> dict:
        totals_possible = self.manifest["expected_total"] if self.manifest else None
        return {
            "booklet_id": self.booklet_id, "exam_id": self.exam_id,
            "config_snapshot": self.config_snapshot, "manifest": self.manifest,
            "entries": self.entries, "history": self.history, "cost": self.cost,
            "totals": {"awarded": sum(e["score"] for e in self.entries.values()),
                       "possible": totals_possible},
            "booklet_status": self.status(),
            "final_report": self.final_report(),
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
