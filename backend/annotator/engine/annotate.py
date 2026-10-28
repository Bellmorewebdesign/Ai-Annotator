"""Turn AI selections into verified PDF annotations.

This module is the bridge between Stage B (AI selections) and Stages C/D (rendering
and verification). It never trusts the model's locations: every selection is matched
against the document text, overlaps are resolved, density is capped, and the output
is verified glyph by glyph. Annotations that fail verification are removed and the
file is written again.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import span_geometry
from .match import match_quote
from .model import DocumentLayout
from .render import AnnotationSpec, hex_to_rgb, write_annotations
from .textindex import DocText, skeleton
from .verify import ExpectedAnnotation, page_fingerprints, verify_output

IMPORTANCE_ORDER = {"high": 0, "medium": 1, "supporting": 2}
# Maximum share of the document's body text that may be highlighted, by density.
COVERAGE_CAP = {"light": 0.08, "balanced": 0.15, "detailed": 0.25}


@dataclass
class Category:
    id: str
    name: str
    color: str
    description: str = ""


@dataclass
class TextSelection:
    id: str
    kind: str  # highlight | underline
    passage_id: str
    quote: str
    category_id: str
    comment: str = ""
    reason: str = ""
    importance: str = "medium"
    occurrence: int = 1


@dataclass
class RegionSelection:
    id: str
    region_id: str
    shape: str  # box | circle
    category_id: str
    comment: str = ""
    reason: str = ""


@dataclass
class PlacedAnnotation:
    id: str
    kind: str
    category_id: str
    category: str
    color: str
    pages: list[int]
    text: str
    comment: str
    reason: str
    importance: str
    match: str  # exact | relocated | approximate | region | caption
    note: str = ""
    spec_ids: list[str] = field(default_factory=list)


@dataclass
class Rejected:
    id: str
    kind: str
    quote: str
    reason: str


@dataclass
class PlacementResult:
    placed: list[PlacedAnnotation] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    specs: list[AnnotationSpec] = field(default_factory=list)
    expected: list[ExpectedAnnotation] = field(default_factory=list)


def _new_id() -> str:
    return "an-" + secrets.token_hex(5)


def _trim_to_free(dt: DocText, start: int, end: int, claimed: set[int]) -> tuple[int, int] | None:
    """Longest contiguous run of unclaimed glyph positions within [start, end], on word boundaries."""
    best: tuple[int, int] | None = None
    run_start = None
    for pos in range(start, end + 2):
        free = pos <= end and (dt.refs[pos] is None or pos not in claimed)
        if free and run_start is None:
            run_start = pos
        elif not free and run_start is not None:
            if best is None or (pos - run_start) > (best[1] - best[0] + 1):
                best = (run_start, pos - 1)
            run_start = None
    if best is None:
        return None
    s, e = best
    text = dt.text
    # Shrink to whole words.
    if s > start and text[s - 1].isalnum():
        while s <= e and text[s].isalnum():
            s += 1
    if e < end and e + 1 < len(text) and text[e + 1].isalnum():
        while e >= s and text[e].isalnum():
            e -= 1
    while s <= e and not text[s].isalnum():
        s += 1
    while e >= s and not text[e].isalnum() and text[e] not in ".!?)”\"'%":
        e -= 1
    if e < s or len(skeleton(text[s:e + 1])) < 8:
        return None
    return s, e


def place_annotations(
    doc: DocumentLayout,
    dt: DocText,
    selections: list[TextSelection],
    region_selections: list[RegionSelection],
    categories: list[Category],
    density: str = "balanced",
    fuzzy_threshold: float = 95.0,
) -> PlacementResult:
    cats = {c.id: c for c in categories}
    fallback_cat = categories[0] if categories else Category("general", "Important", "#FFE066")
    result = PlacementResult()
    claimed: dict[str, set[int]] = {"highlight": set(), "underline": set()}

    body_chars = sum(1 for r in dt.refs if r is not None)
    # Short documents still get room for a few meaningful passages.
    budget = max(COVERAGE_CAP.get(density, 0.15) * body_chars, 500)
    used = 0

    ordered = sorted(enumerate(selections), key=lambda t: (IMPORTANCE_ORDER.get(t[1].importance, 1), t[0]))
    for _, sel in ordered:
        kind = sel.kind if sel.kind in ("highlight", "underline") else "highlight"
        cat = cats.get(sel.category_id, fallback_cat)
        m = match_quote(dt, sel.quote, sel.passage_id, sel.occurrence, fuzzy_threshold)
        if not m.ok:
            result.rejected.append(Rejected(sel.id, kind, sel.quote[:200], m.note or m.status))
            continue
        start, end = m.start, m.end
        positions = {p for p in range(start, end + 1) if dt.refs[p] is not None}
        overlap = positions & claimed[kind]
        note = m.note
        if overlap:
            if len(overlap) >= 0.5 * len(positions):
                result.rejected.append(Rejected(sel.id, kind, sel.quote[:200],
                                                "Overlaps a more important annotation of the same type."))
                continue
            trimmed = _trim_to_free(dt, start, end, claimed[kind])
            if trimmed is None:
                result.rejected.append(Rejected(sel.id, kind, sel.quote[:200],
                                                "Overlaps a more important annotation of the same type."))
                continue
            start, end = trimmed
            positions = {p for p in range(start, end + 1) if dt.refs[p] is not None}
            note = (note + " " if note else "") + "Trimmed to avoid overlapping another annotation."
        if kind == "highlight":
            if used + len(positions) > budget:
                result.rejected.append(Rejected(sel.id, kind, sel.quote[:200],
                                                "Skipped to keep the document from being over-highlighted."))
                continue
            used += len(positions)

        geoms = span_geometry(doc, dt, start, end)
        if not geoms:
            result.rejected.append(Rejected(sel.id, kind, sel.quote[:200], "No visible text to annotate."))
            continue
        claimed[kind] |= positions
        placed = PlacedAnnotation(
            id=sel.id, kind=kind, category_id=cat.id, category=cat.name, color=cat.color,
            pages=[g.page for g in geoms], text=dt.text[start:end + 1].strip(), comment=sel.comment.strip(),
            reason=sel.reason.strip(), importance=sel.importance, match=m.status, note=note.strip(),
        )
        rgb = hex_to_rgb(cat.color)
        for g in geoms:
            spec_id = _new_id()
            seg_text = "".join(
                dt.text[p] for p in range(start, end + 1)
                if dt.refs[p] is not None and dt.refs[p].page == g.page
            )
            underlines = [(*s.underline, max(0.6, 0.07 * s.size)) for s in g.segments]
            # The note is attached to the first piece only so viewers do not repeat it.
            comment = placed.comment if not placed.spec_ids else ""
            result.specs.append(AnnotationSpec(id=spec_id, kind=kind, page=g.page, color=rgb, category=cat.name,
                                               comment=comment, quads=g.quads, underlines=underlines))
            result.expected.append(ExpectedAnnotation(id=spec_id, page=g.page, kind=kind, expected_text=seg_text,
                                                      quads=g.quads))
            placed.spec_ids.append(spec_id)
        result.placed.append(placed)

    regions = {r.id: r for p in doc.pages for r in p.regions}
    for rs in region_selections:
        cat = cats.get(rs.category_id, fallback_cat)
        region = regions.get(rs.region_id)
        if region is None:
            result.rejected.append(Rejected(rs.id, rs.shape, rs.region_id, "Unknown figure or table reference."))
            continue
        page = doc.pages[region.page]
        if region.confidence in ("high", "medium"):
            x0, y0, x1, y1 = region.user_box
            pad = 3.0
            cx0, cy0, cx1, cy1 = page.crop_box
            rect = (max(cx0 + 2, x0 - pad), max(cy0 + 2, y0 - pad), min(cx1 - 2, x1 + pad), min(cy1 - 2, y1 + pad))
            spec_id = _new_id()
            shape = rs.shape if rs.shape in ("box", "circle") else "box"
            result.specs.append(AnnotationSpec(id=spec_id, kind=shape, page=region.page, color=hex_to_rgb(cat.color),
                                               category=cat.name, comment=rs.comment.strip(), rect=rect))
            result.expected.append(ExpectedAnnotation(id=spec_id, page=region.page, kind=shape, expected_text=None))
            result.placed.append(PlacedAnnotation(
                id=rs.id, kind=shape, category_id=cat.id, category=cat.name, color=cat.color, pages=[region.page],
                text=region.caption or f"{region.kind.title()} on page {region.page + 1}", comment=rs.comment.strip(),
                reason=rs.reason.strip(), importance="medium", match="region", spec_ids=[spec_id],
            ))
        elif region.caption_block and region.caption_block in dt.block_spans:
            s, e = dt.block_spans[region.caption_block]
            while e > s and dt.refs[e - 1] is None:
                e -= 1
            positions = {p for p in range(s, e) if dt.refs[p] is not None}
            if positions & claimed["highlight"]:
                result.rejected.append(Rejected(rs.id, rs.shape, region.caption[:200],
                                                "Caption already highlighted."))
                continue
            geoms = span_geometry(doc, dt, s, e - 1)
            if not geoms:
                continue
            claimed["highlight"] |= positions
            placed = PlacedAnnotation(
                id=rs.id, kind="highlight", category_id=cat.id, category=cat.name, color=cat.color,
                pages=[g.page for g in geoms], text=dt.text[s:e].strip(), comment=rs.comment.strip(),
                reason=rs.reason.strip(), importance="medium", match="caption",
                note="The figure's exact area could not be located reliably, so its caption was highlighted instead.",
            )
            for g in geoms:
                spec_id = _new_id()
                seg_text = "".join(dt.text[p] for p in range(s, e) if dt.refs[p] is not None and dt.refs[p].page == g.page)
                result.specs.append(AnnotationSpec(id=spec_id, kind="highlight", page=g.page,
                                                   color=hex_to_rgb(cat.color), category=cat.name,
                                                   comment=placed.comment if not placed.spec_ids else "",
                                                   quads=g.quads))
                result.expected.append(ExpectedAnnotation(id=spec_id, page=g.page, kind="highlight",
                                                          expected_text=seg_text, quads=g.quads))
                placed.spec_ids.append(spec_id)
            result.placed.append(placed)
        else:
            result.rejected.append(Rejected(rs.id, rs.shape, region.caption[:200] or rs.region_id,
                                            "The figure could not be located reliably."))
    return result


@dataclass
class RenderOutcome:
    verified: bool
    removed: list[str]  # placed annotation ids removed after failing verification
    problems: list[str]
    checked: int
    content_preserved: bool = True
    pages_rendered: int = 0


def render_and_verify(src: Path, out: Path, placement: PlacementResult, doc: DocumentLayout | None = None,
                      max_rounds: int = 3) -> RenderOutcome:
    """Write the annotations, verify them, and drop whole annotations that fail until clean."""
    fingerprints = page_fingerprints(src)
    # Scanned pages have no text layer in the PDF; verify against the OCR word boxes instead.
    glyphs = None
    if doc is not None:
        glyphs = {p.index: [(c.text, c.ux0, c.uy0, c.ux1, c.uy1) for c in p.chars] for p in doc.pages if p.is_ocr}
    owner = {sid: p.id for p in placement.placed for sid in p.spec_ids}
    removed: list[str] = []
    report = None
    for _ in range(max_rounds):
        write_annotations(src, out, placement.specs)
        report = verify_output(src, out, placement.expected, src_fingerprints=fingerprints, glyph_override=glyphs)
        if not report.failed:
            break
        bad = {owner.get(sid) for sid in report.failed} - {None}
        for p in placement.placed:
            if p.id in bad:
                removed.append(p.id)
                placement.rejected.append(Rejected(p.id, p.kind, p.text[:200],
                                                   "Removed because it failed the final position check."))
        drop = {sid for p in placement.placed if p.id in bad for sid in p.spec_ids} | set(report.failed)
        placement.placed = [p for p in placement.placed if p.id not in bad]
        placement.specs = [s for s in placement.specs if s.id not in drop]
        placement.expected = [e for e in placement.expected if e.id not in drop]
    assert report is not None
    if report.failed:  # pragma: no cover - would need a failure that survives removal
        write_annotations(src, out, [])
        placement.placed, placement.specs, placement.expected = [], [], []
        report = verify_output(src, out, [], src_fingerprints=fingerprints, glyph_override=glyphs)
    return RenderOutcome(verified=report.ok, removed=removed, problems=report.problems, checked=report.checked,
                         content_preserved=report.content_preserved, pages_rendered=report.pages_rendered)
