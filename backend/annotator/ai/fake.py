"""Deterministic stand-in for Claude, used only by tests (ANNOTATOR_ENV=test).

It reads the same prompt blocks the real model receives and returns schema-valid
responses built from the document's own passages, so the full pipeline (matching,
rendering, verification, summary) can be exercised without network access. It is
not a heuristic annotator and is never used outside the test environment.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from .client import AIError, CallRecord, StructuredResult

_PASSAGE = re.compile(r"^\[(p\d+\.\d+)\](?: \((\w+)\))? (.+)$", re.M)
_REGION = re.compile(r"^\[(p\d+\.[FT]\d+)\] \w+, location reliable", re.M)
_VERIFIED = re.compile(r"^\[(a\d+)\]", re.M)
_COLORS = "yellow|green|blue|pink|orange|purple|red|teal"
_COLOR_RULE = re.compile(rf"(?:highlight\s+|and\s+)?([a-z][a-z ]{{2,30}}?)\s+in\s+({_COLORS})\b", re.I)


class FakeClaude:
    def __init__(self, fail_times: int = 0, error: Exception | None = None, quote_mutator=None):
        self.fail_times = fail_times
        self.error = error or AIError("Scripted failure", "fake_failure")
        self.quote_mutator = quote_mutator
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def structured(self, *, purpose: str, system: str, blocks: list[dict[str, Any]], schema: dict[str, Any],
                   model: str, effort: str | None, max_tokens: int) -> StructuredResult:
        with self._lock:
            self.calls.append(purpose)
            if self.fail_times > 0:
                self.fail_times -= 1
                raise self.error
        text = "\n".join(b["text"] for b in blocks)
        if purpose.startswith("analysis"):
            data = self._analysis(text, schema)
        elif purpose == "plan":
            ids = re.findall(r"Sections: (.+)$", text, re.M)
            sections = [s.strip() for s in ids[-1].split(",")] if ids else []
            data = {"overview": "Scripted plan overview.", "themes": ["scripted theme"],
                    "sections": [{"section_id": s, "guidance": "Focus on claims."} for s in sections]}
        elif purpose == "summary":
            data = self._summary(text)
        elif purpose == "categories":
            data = self._categories(text)
        else:  # pragma: no cover
            raise AIError("Unknown purpose", "fake_unknown")
        cached = sum(len(b["text"]) for b in blocks if b.get("cache_control"))
        record = CallRecord(purpose=purpose, model=model, input_tokens=len(text) // 4,
                            output_tokens=200, cache_read_tokens=cached // 4 if purpose == "summary" else 0,
                            cache_write_tokens=cached // 4 if purpose == "analysis" else 0, cost_usd=0.0)
        return StructuredResult(data=data, record=record)

    def _analysis(self, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        props = schema["properties"]["text_annotations"]["items"]["properties"]
        cats = props["category_id"]["enum"]
        kinds = props["kind"]["enum"]
        anns = []
        for n, m in enumerate(p for p in _PASSAGE.finditer(text) if p.group(2) in (None, "ocr")):
            words = m.group(3).split()
            if len(words) < 8:
                continue
            quote = " ".join(words[2:9])
            if self.quote_mutator:
                quote = self.quote_mutator(quote)
            anns.append({
                "passage_id": m.group(1), "quote": quote, "occurrence": 1,
                "kind": kinds[len(anns) % len(kinds)] if len(anns) % 3 == 2 else "highlight",
                "category_id": cats[len(anns) % len(cats)], "importance": "high" if n == 0 else "medium",
                "comment": "Scripted note grounded in this passage.", "reason": "Scripted selection.",
            })
            if len(anns) >= 6:
                break
        data: dict[str, Any] = {
            "document_overview": "Scripted overview of the document.",
            "instruction_notes": [{"kind": "assumption", "message": "Scripted assumption note."}],
            "text_annotations": anns,
        }
        if "region_annotations" in schema["properties"]:
            regions = _REGION.findall(text)
            data["region_annotations"] = [
                {"region_id": r, "shape": "box", "category_id": cats[0], "comment": "Key figure.",
                 "reason": "Scripted."} for r in regions[:1]
            ]
        return data

    def _summary(self, text: str) -> dict[str, Any]:
        ids = _VERIFIED.findall(text)
        return {
            "title": "Scripted summary",
            "purpose": "The document argues a scripted point.",
            "sections": [
                {"heading": "Supporting evidence", "points": [{"text": "Evidence point.", "annotation_ids": ids[:2]}]},
                {"heading": "Limitations", "points": [{"text": "A limitation.", "annotation_ids": ids[2:3] + ["zz9"]}]},
            ],
            "rubric_alignment": [],
            "takeaways": ["Scripted takeaway."],
        }

    def _categories(self, text: str) -> dict[str, Any]:
        cats = []
        for name, color in _COLOR_RULE.findall(text):
            cats.append({"name": name.strip().split(",")[0].title()[:40], "color": color.lower(),
                         "description": "", "color_from_instructions": True})
        return {"categories": cats, "notes": [], "wants_underline": "underline" in text.lower(),
                "wants_comments": "comment" in text.lower() or "note" in text.lower()}
