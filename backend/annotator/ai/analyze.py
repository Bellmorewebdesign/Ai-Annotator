"""Orchestrates Claude calls: analysis (single pass or planned sections), summary, categories."""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import Settings
from ..engine.annotate import Category, PlacedAnnotation, RegionSelection, TextSelection
from ..engine.model import DocumentLayout
from . import prompts
from .client import CallRecord, StructuredClient

CHARS_PER_TOKEN = 3.5
IMPORTANCE = ("high", "medium", "supporting")


@dataclass
class AnalysisRequest:
    instructions: str
    rubric: str
    categories: list[Category]
    density: str = "balanced"
    allow_underline: bool = True
    allow_comments: bool = True
    allow_shapes: bool = True


@dataclass
class AnalysisResult:
    mode: str  # single | sectioned
    overview: str
    notes: list[dict[str, str]]
    selections: list[TextSelection]
    region_selections: list[RegionSelection]
    calls: list[CallRecord] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    dropped: int = 0  # malformed items discarded

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["calls"] = [c.to_dict() for c in self.calls]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnalysisResult:
        return cls(
            mode=d["mode"], overview=d["overview"], notes=d["notes"],
            selections=[TextSelection(**s) for s in d["selections"]],
            region_selections=[RegionSelection(**r) for r in d["region_selections"]],
            calls=[CallRecord(**c) for c in d.get("calls", [])], plan=d.get("plan"), dropped=d.get("dropped", 0),
        )


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def plan_sections(doc: DocumentLayout, target_tokens: int) -> list[tuple[str, range]]:
    sections: list[tuple[str, range]] = []
    start = 0
    acc = 0
    for i, page in enumerate(doc.pages):
        t = estimate_tokens(" ".join(b.text for b in page.blocks))
        if acc and acc + t > target_tokens:
            sections.append((f"S{len(sections) + 1}", range(start, i)))
            start, acc = i, 0
        acc += t
    sections.append((f"S{len(sections) + 1}", range(start, len(doc.pages))))
    return sections


def _request_block(req: AnalysisRequest) -> str:
    return prompts.format_request(req.instructions, req.rubric, req.categories, req.density,
                                  req.allow_underline, req.allow_comments, req.allow_shapes)


def _parse_analysis(data: dict[str, Any], req: AnalysisRequest, valid_passages: set[str], valid_regions: set[str],
                    id_prefix: str, counter: list[int]) -> tuple[list[TextSelection], list[RegionSelection], int]:
    cat_ids = {c.id for c in req.categories}
    sels: list[TextSelection] = []
    regs: list[RegionSelection] = []
    dropped = 0
    for item in data.get("text_annotations") or []:
        try:
            quote = str(item["quote"]).strip()
            kind = item.get("kind", "highlight")
            if kind == "underline" and not req.allow_underline:
                kind = "highlight"
            if kind not in ("highlight", "underline") or not quote or item.get("category_id") not in cat_ids:
                dropped += 1
                continue
            counter[0] += 1
            sels.append(TextSelection(
                id=f"{id_prefix}{counter[0]}", kind=kind, passage_id=str(item.get("passage_id", "")),
                quote=quote, category_id=item["category_id"],
                comment=str(item.get("comment") or "")[:400] if req.allow_comments else "",
                reason=str(item.get("reason") or "")[:400],
                importance=item.get("importance") if item.get("importance") in IMPORTANCE else "medium",
                occurrence=max(1, int(item.get("occurrence") or 1)),
            ))
        except (KeyError, TypeError, ValueError):
            dropped += 1
    if req.allow_shapes:
        for item in data.get("region_annotations") or []:
            try:
                if item.get("region_id") not in valid_regions or item.get("category_id") not in cat_ids:
                    dropped += 1
                    continue
                counter[0] += 1
                regs.append(RegionSelection(
                    id=f"{id_prefix}{counter[0]}", region_id=item["region_id"],
                    shape=item.get("shape") if item.get("shape") in ("box", "circle") else "box",
                    category_id=item["category_id"],
                    comment=str(item.get("comment") or "")[:400] if req.allow_comments else "",
                    reason=str(item.get("reason") or "")[:400],
                ))
            except (KeyError, TypeError):
                dropped += 1
    return sels, regs, dropped


def _cap(sels: list[TextSelection], doc: DocumentLayout, density: str) -> list[TextSelection]:
    """Hard ceiling on count so a runaway response cannot flood the document."""
    _, _, hi, _ = prompts.DENSITY_GUIDE.get(density, prompts.DENSITY_GUIDE["balanced"])
    limit = int(len(doc.pages) * hi * 1.5) + 6
    if len(sels) <= limit:
        return sels
    order = {k: i for i, k in enumerate(IMPORTANCE)}
    keep = sorted(sels, key=lambda s: order.get(s.importance, 1))[:limit]
    keep_ids = {s.id for s in keep}
    return [s for s in sels if s.id in keep_ids]


def analyze_document(
    client: StructuredClient,
    settings: Settings,
    doc: DocumentLayout,
    req: AnalysisRequest,
    on_progress: Callable[[int, int], None] | None = None,
) -> AnalysisResult:
    document_text = prompts.format_document(doc)
    request_text = _request_block(req)
    valid_passages = {b.id for p in doc.pages for b in p.blocks}
    valid_regions = {r.id for p in doc.pages for r in p.regions}
    category_ids = [c.id for c in req.categories]
    schema = prompts.analysis_schema(category_ids, req.allow_underline, req.allow_shapes and bool(valid_regions))
    counter = [0]

    if estimate_tokens(document_text) <= settings.single_pass_token_limit:
        if on_progress:
            on_progress(0, 1)
        res = client.structured(
            purpose="analysis", system=prompts.SYSTEM_PROMPT,
            blocks=analysis_blocks(document_text, request_text, prompts.ANALYSIS_TASK),
            schema=schema, model=settings.model, effort=settings.analysis_effort,
            max_tokens=settings.ai_max_output_tokens,
        )
        sels, regs, dropped = _parse_analysis(res.data, req, valid_passages, valid_regions, "a", counter)
        if on_progress:
            on_progress(1, 1)
        return AnalysisResult(
            mode="single", overview=str(res.data.get("document_overview", "")),
            notes=_notes(res.data), selections=_cap(sels, doc, req.density), region_selections=regs,
            calls=[res.record], dropped=dropped,
        )

    # Long document: plan over a condensed outline, then analyse sections with the plan.
    sections = plan_sections(doc, settings.section_token_target)
    total = len(sections) + 1
    done = 0
    if on_progress:
        on_progress(done, total)
    plan_res = client.structured(
        purpose="plan", system=prompts.SYSTEM_PROMPT,
        blocks=[{"type": "text", "text": prompts.format_outline(doc, sections)},
                {"type": "text", "text": request_text},
                {"type": "text", "text": prompts.PLAN_TASK + "\nSections: " + ", ".join(s for s, _ in sections)}],
        schema=prompts.PLAN_SCHEMA, model=settings.model, effort=settings.analysis_effort,
        max_tokens=16000,
    )
    plan = plan_res.data
    calls = [plan_res.record]
    done += 1
    if on_progress:
        on_progress(done, total)

    def run(section: tuple[str, range]):
        sid, pages = section
        text = prompts.format_document(doc, pages)
        return client.structured(
            purpose=f"analysis:{sid}", system=prompts.SYSTEM_PROMPT,
            blocks=[{"type": "text", "text": text},
                    {"type": "text", "text": request_text},
                    {"type": "text", "text": prompts.section_task(sid, pages, plan)}],
            schema=schema, model=settings.model, effort=settings.analysis_effort,
            max_tokens=settings.ai_max_output_tokens,
        )

    results = []
    with ThreadPoolExecutor(max_workers=max(1, settings.ai_concurrency)) as pool:
        for r in pool.map(run, sections):  # map preserves section order
            results.append(r)
            done += 1
            if on_progress:
                on_progress(done, total)

    sels: list[TextSelection] = []
    regs: list[RegionSelection] = []
    notes: list[dict[str, str]] = []
    dropped = 0
    for r in results:
        s, g, d = _parse_analysis(r.data, req, valid_passages, valid_regions, "a", counter)
        sels.extend(s)
        regs.extend(g)
        dropped += d
        notes.extend(_notes(r.data))
        calls.append(r.record)
    seen: set[str] = set()
    unique_notes = []
    for n in notes:
        if n["message"] not in seen:
            seen.add(n["message"])
            unique_notes.append(n)
    return AnalysisResult(mode="sectioned", overview=str(plan.get("overview", "")), notes=unique_notes,
                          selections=_cap(sels, doc, req.density), region_selections=regs, calls=calls,
                          plan=plan, dropped=dropped)


def analysis_blocks(document_text: str, request_text: str, task: str) -> list[dict[str, Any]]:
    """Document and request are cache breakpoints shared by analysis and summary calls."""
    return [
        {"type": "text", "text": document_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": request_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": task},
    ]


def _notes(data: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for n in data.get("instruction_notes") or []:
        if isinstance(n, dict) and n.get("message"):
            out.append({"kind": str(n.get("kind", "note")), "message": str(n["message"])[:500]})
    return out


# ----------------------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------------------

def format_verified(placed: list[PlacedAnnotation]) -> str:
    lines = ["<verified_annotations>"]
    for p in placed:
        pages = ", ".join(str(i + 1) for i in sorted(set(p.pages)))
        text = prompts.neutralize(" ".join(p.text.split()))
        if len(text) > 400:
            text = text[:400] + "..."
        note = f" | note: {p.comment}" if p.comment else ""
        lines.append(f'[{p.id}] page {pages} | {p.category} | {p.kind} | "{text}"{note}')
    lines.append("</verified_annotations>")
    return "\n".join(lines)


def summarize(
    client: StructuredClient,
    settings: Settings,
    doc: DocumentLayout,
    req: AnalysisRequest,
    analysis: AnalysisResult,
    placed: list[PlacedAnnotation],
) -> tuple[dict[str, Any], CallRecord]:
    request_text = _request_block(req)
    task = prompts.SUMMARY_TASK + "\n" + format_verified(placed)
    if analysis.mode == "single":
        blocks = analysis_blocks(prompts.format_document(doc), request_text, task)
    else:
        plan = analysis.plan or {}
        context = ("<reading_plan>\n" + str(plan.get("overview", "")) + "\nThemes: "
                   + "; ".join(plan.get("themes") or []) + "\n</reading_plan>")
        blocks = [{"type": "text", "text": context}, {"type": "text", "text": request_text},
                  {"type": "text", "text": task}]
    res = client.structured(purpose="summary", system=prompts.SYSTEM_PROMPT, blocks=blocks,
                            schema=prompts.SUMMARY_SCHEMA, model=settings.model, effort=settings.summary_effort,
                            max_tokens=16000)
    return res.data, res.record


# ----------------------------------------------------------------------------------------
# Categories from instructions
# ----------------------------------------------------------------------------------------

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "category"


def suggest_categories(client: StructuredClient, settings: Settings, instructions: str,
                       rubric: str) -> tuple[dict[str, Any], CallRecord]:
    text = "<instructions>\n" + (prompts.neutralize(instructions.strip()) or "(none)") + "\n</instructions>"
    if rubric.strip():
        text += "\n<attached_instructions>\n" + prompts.neutralize(rubric.strip()) + "\n</attached_instructions>"
    res = client.structured(purpose="categories", system=prompts.CATEGORY_SYSTEM,
                            blocks=[{"type": "text", "text": text}], schema=prompts.category_schema(),
                            model=settings.light_model, effort=settings.light_effort, max_tokens=4000)
    cats = []
    used: set[str] = set()
    for c in res.data.get("categories") or []:
        name = str(c.get("name", "")).strip()[:40]
        if not name:
            continue
        cid = slugify(name)
        while cid in used:
            cid += "-2"
        used.add(cid)
        cats.append({
            "id": cid, "name": name,
            "color": prompts.PALETTE.get(c.get("color"), prompts.PALETTE["yellow"]),
            "description": str(c.get("description", ""))[:200],
            "color_from_instructions": bool(c.get("color_from_instructions")),
        })
    out = {
        "categories": cats[:12],
        "notes": [str(n)[:300] for n in (res.data.get("notes") or [])][:8],
        "wants_underline": bool(res.data.get("wants_underline")),
        "wants_comments": bool(res.data.get("wants_comments")),
        "used_defaults": not cats,
    }
    if not cats:
        out["categories"] = [asdict(c) for c in prompts.DEFAULT_CATEGORIES]
    return out, res.record
