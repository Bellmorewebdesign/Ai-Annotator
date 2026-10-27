"""Turn matched text spans into precise per-line geometry in PDF user space.

A selection becomes one rectangle per line fragment it touches, spanning exactly
from the first selected glyph to the last selected glyph on that line. Rectangles are
computed in the text-aligned frame (so rotation is handled once, in
:class:`FrameTransform`) and clipped to the line's slot so highlights on adjacent
lines never overlap, and to the page crop box so nothing extends off the page.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .model import DocumentLayout
from .textindex import DocText

Quad = tuple[float, float, float, float, float, float, float, float]


@dataclass
class LineSegment:
    page: int
    line: int
    chars: list[int]
    # Frame-space rectangle and baseline for this segment.
    fx0: float
    fy0: float
    fx1: float
    fy1: float
    baseline: float
    size: float
    group: int
    quad: Quad = (0, 0, 0, 0, 0, 0, 0, 0)
    underline: tuple[float, float, float, float] = (0, 0, 0, 0)  # x0, y0, x1, y1 user space


@dataclass
class PageGeometry:
    page: int
    segments: list[LineSegment] = field(default_factory=list)

    @property
    def quads(self) -> list[Quad]:
        return [s.quad for s in self.segments]

    @property
    def rect(self) -> tuple[float, float, float, float]:
        xs = [v for q in self.quads for v in q[0::2]]
        ys = [v for q in self.quads for v in q[1::2]]
        return min(xs), min(ys), max(xs), max(ys)


def span_geometry(doc: DocumentLayout, dt: DocText, start: int, end: int) -> list[PageGeometry]:
    """Geometry for text positions [start, end] (inclusive), grouped by page."""
    runs: list[tuple[int, int, list[int]]] = []  # (page, line, chars)
    for pos in range(start, end + 1):
        ref = dt.refs[pos]
        if ref is None:
            continue
        line = doc.pages[ref.page].chars[ref.char].line
        if runs and runs[-1][0] == ref.page and runs[-1][1] == line:
            runs[-1][2].append(ref.char)
        else:
            runs.append((ref.page, line, [ref.char]))

    pages: dict[int, PageGeometry] = {}
    for ri, (pi, li, cidx) in enumerate(runs):
        page = doc.pages[pi]
        line = page.lines[li]
        chosen = list(cidx)
        if ri < len(runs) - 1 and line.elided and cidx[-1] == _last_kept(line):
            chosen.extend(line.elided)  # include the hyphen of a word broken across lines
        chars = [page.chars[i] for i in chosen]
        ft = page.groups[line.group]
        x0 = min(c.fx0 for c in chars)
        x1 = max(c.fx1 for c in chars)
        y0 = min(c.fy0 for c in chars)
        y1 = max(c.fy1 for c in chars)
        y0 = max(y0, line.slot_top)
        y1 = min(y1, line.slot_bottom)
        # Clip to the visible page.
        x0, x1 = max(0.0, x0), min(ft.width, x1)
        y0, y1 = max(0.0, y0), min(ft.height, y1)
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            continue
        base = statistics.median(c.baseline for c in chars)
        size = statistics.median(c.size for c in chars)
        seg = LineSegment(page=pi, line=li, chars=chosen, fx0=x0, fy0=y0, fx1=x1, fy1=y1,
                          baseline=base, size=size, group=line.group)
        ul = ft.to_user(x0, y0)
        ur = ft.to_user(x1, y0)
        ll = ft.to_user(x0, y1)
        lr = ft.to_user(x1, y1)
        # Acrobat-compatible QuadPoints order: upper-left, upper-right, lower-left, lower-right
        # (relative to the text direction).
        seg.quad = (ul[0], ul[1], ur[0], ur[1], ll[0], ll[1], lr[0], lr[1])
        uy = min(base + 0.12 * size, y1)
        a = ft.to_user(x0, uy)
        b = ft.to_user(x1, uy)
        seg.underline = (a[0], a[1], b[0], b[1])
        pages.setdefault(pi, PageGeometry(page=pi)).segments.append(seg)
    return [pages[k] for k in sorted(pages)]


def _last_kept(line) -> int:
    kept = [c for c in line.chars if c not in line.elided]
    return kept[-1] if kept else -1
