"""Layout analysis: characters -> lines -> columns -> reading order -> paragraphs.

The approach is deliberately geometric and deterministic:

1. **Line fragments.** Characters are walked in content-stream order and joined while
   they share a baseline band and the horizontal gap stays below roughly one em.
   Fragments on the same visual line are then merged (again only across small gaps),
   so text in neighbouring columns never fuses into one line.
2. **Gutters.** Vertical whitespace channels are found from the x-projection of
   narrow lines. A channel is only accepted as a column gutter when there is
   substantial text on both sides, which rejects bullets, numbering and indents.
3. **Bands.** Lines that cross a gutter (titles, abstracts, full-width figures) split
   the page into horizontal bands. Inside each band, columns are read left to right,
   each from top to bottom. This handles the common academic layout of a full-width
   title block followed by two columns.
4. **Paragraphs.** Consecutive lines in the same column are grouped into blocks using
   vertical gaps, first-line indents, short last lines and font-size changes.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter, defaultdict

from .model import Block, Char, DocumentLayout, Line, PageLayout

HYPHENS = {"-", "­", "‐", "‑"}
_CAPTION_RE = re.compile(r"^\s*(figure|fig\.|table|chart|exhibit|plate|scheme)\s*[A-Z]?[\dIVX]+", re.I)
_PAGE_NUM_RE = re.compile(r"^\W*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\W*$", re.I)
_REFERENCES_RE = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|works cited|literature cited)\s*$", re.I)


# ----------------------------------------------------------------------------------------
# Lines
# ----------------------------------------------------------------------------------------

def _v_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


class _Frag:
    __slots__ = ("chars", "x0", "x1", "y0", "y1", "base_y0", "base_y1", "size", "group")

    def __init__(self, idx: int, c: Char):
        self.chars = [idx]
        self.x0, self.x1, self.y0, self.y1 = c.fx0, c.fx1, c.fy0, c.fy1
        self.base_y0, self.base_y1 = c.fy0, c.fy1
        self.size = c.size
        self.group = c.group

    def add(self, idx: int, c: Char) -> None:
        self.chars.append(idx)
        self.x0, self.x1 = min(self.x0, c.fx0), max(self.x1, c.fx1)
        self.y0, self.y1 = min(self.y0, c.fy0), max(self.y1, c.fy1)
        if c.fy1 - c.fy0 > self.base_y1 - self.base_y0:
            self.base_y0, self.base_y1 = c.fy0, c.fy1
            self.size = c.size

    def merge(self, other: _Frag) -> None:
        self.chars.extend(other.chars)
        self.x0, self.x1 = min(self.x0, other.x0), max(self.x1, other.x1)
        self.y0, self.y1 = min(self.y0, other.y0), max(self.y1, other.y1)
        if other.base_y1 - other.base_y0 > self.base_y1 - self.base_y0:
            self.base_y0, self.base_y1, self.size = other.base_y0, other.base_y1, other.size

    @property
    def base_h(self) -> float:
        return max(self.base_y1 - self.base_y0, 0.1)


def _same_band(f: _Frag, y0: float, y1: float) -> bool:
    h = min(f.base_h, max(y1 - y0, 0.1))
    return _v_overlap(f.base_y0, f.base_y1, y0, y1) >= 0.4 * h


def _build_fragments(chars: list[Char], indices: list[int]) -> list[_Frag]:
    frags: list[_Frag] = []
    cur: _Frag | None = None
    last: Char | None = None
    for idx in indices:
        c = chars[idx]
        if cur is not None and last is not None and _same_band(cur, c.fy0, c.fy1):
            em = max(c.size, cur.size, 1.0)
            gap = c.fx0 - last.fx1
            if -0.6 * em <= gap <= 0.9 * em:
                cur.add(idx, c)
                last = c
                continue
        cur = _Frag(idx, c)
        frags.append(cur)
        last = c
    return _merge_fragments(frags, chars)


def _merge_fragments(frags: list[_Frag], chars: list[Char]) -> list[_Frag]:
    """Merge fragments that sit on the same visual line with a small gap between them."""
    changed = True
    while changed:
        changed = False
        frags.sort(key=lambda f: ((f.base_y0 + f.base_y1) / 2, f.x0))
        out: list[_Frag] = []
        for f in frags:
            merged = False
            for g in reversed(out[-8:]):
                if not _same_band(g, f.base_y0, f.base_y1):
                    continue
                em = max(f.size, g.size, 1.0)
                left, right = (g, f) if g.x0 <= f.x0 else (f, g)
                gap = right.x0 - left.x1
                if -0.3 * em <= gap <= 0.9 * em:
                    g.merge(f)
                    merged = True
                    changed = True
                    break
            if not merged:
                out.append(f)
        frags = out
    for f in frags:
        f.chars.sort(key=lambda i: (chars[i].fx0, chars[i].fx1))
    return frags


# ----------------------------------------------------------------------------------------
# Columns and reading order
# ----------------------------------------------------------------------------------------

def _find_gutters(lines: list[Line], chars: list[Char]) -> list[tuple[float, float]]:
    if len(lines) < 6:
        return []
    ax0 = min(ln.fx0 for ln in lines)
    ax1 = max(ln.fx1 for ln in lines)
    width = ax1 - ax0
    if width < 100:
        return []
    narrow = [ln for ln in lines if (ln.fx1 - ln.fx0) < 0.6 * width]
    if len(narrow) < 6:
        return []
    sizes = [ln.size for ln in lines]
    em = statistics.median(sizes) if sizes else 10.0
    min_gap = max(6.0, 0.8 * em)
    bins = int(width) + 1
    cover = [0] * bins
    for ln in narrow:
        a = max(0, int(ln.fx0 - ax0))
        b = min(bins - 1, int(ln.fx1 - ax0))
        for k in range(a, b + 1):
            cover[k] += 1
    tolerance = max(1, int(0.03 * len(narrow)))
    gutters: list[tuple[float, float]] = []
    k = 0
    while k < bins:
        if cover[k] <= tolerance:
            s = k
            while k < bins and cover[k] <= tolerance:
                k += 1
            g0, g1 = ax0 + s, ax0 + k
            if (g1 - g0) >= min_gap and g0 > ax0 + 0.15 * width and g1 < ax1 - 0.15 * width:
                gutters.append((g0, g1))
        else:
            k += 1
    # Require substantial text on both sides of each gutter.
    accepted: list[tuple[float, float]] = []
    total_chars = sum(len(ln.chars) for ln in narrow) or 1
    bounds = [ax0] + [g for gut in gutters for g in gut] + [ax1]
    for gi, gut in enumerate(gutters):
        left_lo, left_hi = bounds[2 * gi], gut[0]
        right_lo, right_hi = gut[1], bounds[2 * gi + 3]
        left = [ln for ln in narrow if ln.fx0 >= left_lo - 1 and ln.fx1 <= left_hi + 1]
        right = [ln for ln in narrow if ln.fx0 >= right_lo - 1 and ln.fx1 <= right_hi + 1]
        lc = sum(len(ln.chars) for ln in left)
        rc = sum(len(ln.chars) for ln in right)
        lw = max((ln.fx1 - ln.fx0 for ln in left), default=0)
        rw = max((ln.fx1 - ln.fx0 for ln in right), default=0)
        if (len(left) >= 3 and len(right) >= 3 and lc >= 0.12 * total_chars and rc >= 0.12 * total_chars
                and lw >= 0.15 * width and rw >= 0.15 * width):
            accepted.append(gut)
    return accepted


def _reading_order(lines: list[Line], gutters: list[tuple[float, float]]) -> list[Line]:
    if not lines:
        return []

    def col_of(ln: Line) -> int | None:
        c = 0
        for g0, g1 in gutters:
            if ln.fx1 <= g0 + 1:
                return c
            if ln.fx0 >= g1 - 1:
                c += 1
                continue
            return None  # crosses this gutter
        return c

    ordered = sorted(lines, key=lambda ln: (ln.cy, ln.fx0))
    result: list[Line] = []
    band: list[Line] = []
    band_no = 0

    def flush() -> None:
        nonlocal band, band_no
        if not band:
            return
        by_col: dict[int, list[Line]] = defaultdict(list)
        for ln in band:
            ln.band = band_no
            by_col[ln.column].append(ln)
        for col in sorted(by_col):
            result.extend(_rows_in_order(by_col[col]))
        band = []
        band_no += 1

    for ln in ordered:
        col = col_of(ln) if gutters else 0
        if col is None:
            flush()
            ln.column = -1
            ln.band = band_no
            band_no += 1
            result.append(ln)
        else:
            ln.column = col
            band.append(ln)
    flush()
    return result


def _rows_in_order(lines: list[Line]) -> list[Line]:
    """Order lines top to bottom; fragments that share a row are read left to right."""
    lines = sorted(lines, key=lambda ln: ln.cy)
    rows: list[list[Line]] = []
    for ln in lines:
        if rows:
            row = rows[-1]
            ref = row[0]
            if _v_overlap(ref.fy0, ref.fy1, ln.fy0, ln.fy1) >= 0.5 * min(ref.height, ln.height):
                row.append(ln)
                continue
        rows.append([ln])
    out: list[Line] = []
    for row in rows:
        out.extend(sorted(row, key=lambda ln: ln.fx0))
    return out


# ----------------------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------------------

def line_text_parts(line: Line, chars: list[Char]) -> list[tuple[str, int]]:
    """Return (text, char index or -1) pairs for a line, inserting spaces between words."""
    parts: list[tuple[str, int]] = []
    prev: Char | None = None
    for idx in line.chars:
        c = chars[idx]
        if prev is not None:
            gap = c.fx0 - prev.fx1
            em = max(c.size, prev.size, 1.0)
            if gap > 0.17 * em or (c.space_before and gap > -0.05 * em):
                parts.append((" ", -1))
        parts.append((c.text, idx))
        prev = c
    return parts


def _is_hyphen_break(prev_line: Line, next_line: Line, chars: list[Char]) -> bool:
    if len(prev_line.chars) < 2 or not next_line.chars:
        return False
    last = chars[prev_line.chars[-1]].text
    before = chars[prev_line.chars[-2]].text
    first = chars[next_line.chars[0]].text
    return last in HYPHENS and before.isalpha() and first.islower()


# ----------------------------------------------------------------------------------------
# Page analysis
# ----------------------------------------------------------------------------------------

def analyze_page(page: PageLayout) -> None:
    """Populate ``page.lines`` (reading order) and ``page.blocks`` for a non-OCR page."""
    chars = page.chars
    by_group: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(chars):
        by_group[c.group].append(i)

    all_ordered: list[Line] = []
    for gi in sorted(by_group):
        frags = _build_fragments(chars, by_group[gi])
        lines = [
            Line(index=-1, group=gi, chars=f.chars, fx0=f.x0, fy0=f.base_y0, fx1=f.x1, fy1=f.base_y1,
                 size=f.size)
            for f in frags
        ]
        gutters = _find_gutters(lines, chars)
        all_ordered.extend(_reading_order(lines, gutters))

    for i, ln in enumerate(all_ordered):
        ln.index = i
        for ci in ln.chars:
            chars[ci].line = i
    page.lines = all_ordered
    _assign_slots(page)
    page.blocks = _group_blocks(page)


def _assign_slots(page: PageLayout) -> None:
    """Limit each line's highlight height so neighbouring highlights never overlap."""
    by_col: dict[tuple[int, int, int], list[Line]] = defaultdict(list)
    for ln in page.lines:
        by_col[(ln.group, ln.band, ln.column)].append(ln)
    for lines in by_col.values():
        lines = sorted(lines, key=lambda ln: ln.cy)
        for a, b in zip(lines, lines[1:], strict=False):
            if _v_overlap(a.fx0, a.fx1, b.fx0, b.fx1) <= 0:
                continue
            if a.fy1 > b.fy0 and b.cy > a.cy:
                split = (a.fy1 + b.fy0) / 2
                a.slot_bottom = min(a.slot_bottom, split)
                b.slot_top = max(b.slot_top, split)


def _group_blocks(page: PageLayout) -> list[Block]:
    chars = page.chars
    blocks: list[list[Line]] = []
    cur: list[Line] = []
    col_extent: dict[tuple[int, int, int], tuple[float, float]] = {}
    for ln in page.lines:
        key = (ln.group, ln.band, ln.column)
        lo, hi = col_extent.get(key, (ln.fx0, ln.fx1))
        col_extent[key] = (min(lo, ln.fx0), max(hi, ln.fx1))

    for ln in page.lines:
        if not cur:
            cur = [ln]
            continue
        prev = cur[-1]
        if _starts_new_block(prev, ln, cur, chars, col_extent):
            blocks.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append(cur)

    out: list[Block] = []
    for bi, blines in enumerate(blocks):
        size = statistics.median([ln.size for ln in blines])
        out.append(Block(id=f"p{page.index + 1}.{bi + 1}", page=page.index,
                         lines=[ln.index for ln in blines], size=size))
    for b in out:
        b.text = block_text(page, b)
    return out


def _starts_new_block(prev: Line, ln: Line, cur: list[Line], chars: list[Char],
                      col_extent: dict[tuple[int, int, int], tuple[float, float]]) -> bool:
    if (prev.group, prev.band, prev.column) != (ln.group, ln.band, ln.column) or ln.column == -1 or prev.column == -1:
        # Full-width lines form their own run; consecutive full-width lines may continue.
        if not (prev.column == -1 and ln.column == -1):
            return True
    em = max(prev.size, ln.size, 1.0)
    # Same row (e.g. table cells) stays together.
    if _v_overlap(prev.fy0, prev.fy1, ln.fy0, ln.fy1) >= 0.5 * min(prev.height, ln.height):
        return False
    if abs(prev.size - ln.size) > 0.15 * max(prev.size, ln.size):
        return True
    gap = ln.fy0 - prev.fy1
    typical = statistics.median([b.fy0 - a.fy1 for a, b in zip(cur, cur[1:], strict=False)]) if len(cur) > 1 else None
    if gap > 0.75 * em and (typical is None or gap > typical + 0.4 * em):
        return True
    if gap < -0.5 * em:  # moved up: a new column region or out-of-order fragment
        return True
    lo, hi = col_extent[(ln.group, ln.band, ln.column)]
    prev_text = "".join(chars[i].text for i in prev.chars[-2:])
    prev_short = prev.fx1 < hi - 2.5 * em
    if prev_short and prev_text and prev_text[-1] in ".?!:;”\")" and ln.fx0 > lo + 0.8 * em:
        return True
    if prev_short and prev_text and prev_text[-1] in ".?!:”\"" and len(cur) >= 1:
        # A short line ending a sentence usually ends the paragraph when the next line
        # starts at the column edge with a capital letter.
        first = chars[ln.chars[0]].text if ln.chars else ""
        if first.isupper() or first.isdigit():
            return True
    if ln.fx0 > lo + 1.0 * em and ln.fx0 > prev.fx0 + 0.8 * em and len(cur) >= 1 and not prev_short:
        return True  # first-line indent
    return False


def block_text(page: PageLayout, block: Block) -> str:
    return "".join(t for t, _ in block_text_map(page, block))


def block_text_map(page: PageLayout, block: Block) -> list[tuple[str, int]]:
    """Text of a block as (piece, char index or -1) pairs, de-hyphenating line breaks.

    Elided hyphens are recorded on the line so geometry can include them when a
    selection continues onto the next line.
    """
    chars = page.chars
    parts: list[tuple[str, int]] = []
    lines = [page.lines[i] for i in block.lines]
    for li, ln in enumerate(lines):
        ln.elided = []
        line_parts = line_text_parts(ln, chars)
        nxt = lines[li + 1] if li + 1 < len(lines) else None
        if nxt is not None and _is_hyphen_break(ln, nxt, chars):
            ln.elided = [ln.chars[-1]]
            line_parts = line_parts[:-1]
            parts.extend(line_parts)
            continue
        parts.extend(line_parts)
        if nxt is not None:
            parts.append((" ", -1))
    return parts


# ----------------------------------------------------------------------------------------
# Document-level roles
# ----------------------------------------------------------------------------------------

def _norm_hf(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"\d+", "#", t)
    return re.sub(r"\s+", " ", t).strip()


def assign_roles(doc: DocumentLayout) -> None:
    sizes: list[float] = []
    for p in doc.pages:
        for b in p.blocks:
            sizes.extend([b.size] * max(1, len(b.text)))
    doc.body_size = statistics.median(sizes) if sizes else 10.0
    body = doc.body_size

    # Headers / footers: short blocks near the top or bottom edge that repeat across
    # pages, or that are just a page number.
    edge_counts: Counter[str] = Counter()
    candidates: list[tuple[Block, str, str]] = []
    for p in doc.pages:
        if p.is_ocr:
            continue
        for b in p.blocks:
            lines = [p.lines[i] for i in b.lines]
            if len(lines) > 2 or len(b.text) > 160:
                continue
            ft = p.groups[lines[0].group]
            top = min(ln.fy0 for ln in lines)
            bottom = max(ln.fy1 for ln in lines)
            where = "header" if bottom < 0.09 * ft.height else "footer" if top > 0.91 * ft.height else ""
            if where:
                key = _norm_hf(b.text)
                edge_counts[key] += 1
                candidates.append((b, where, key))
    multi_page = len(doc.pages) >= 2
    for b, where, key in candidates:
        if _PAGE_NUM_RE.match(b.text) or (multi_page and edge_counts[key] >= 2):
            b.role = where

    in_references = False
    for p in doc.pages:
        if p.is_ocr:
            continue
        for b in p.blocks:
            if b.role in ("header", "footer"):
                continue
            text = b.text.strip()
            n_lines = len(b.lines)
            heavy = _mostly_bold(p, b)
            if _REFERENCES_RE.match(text):
                b.role = "heading"
                in_references = True
                continue
            if _CAPTION_RE.match(text):
                b.role = "caption"
                continue
            if n_lines <= 3 and len(text) <= 160 and (b.size >= 1.12 * body or (heavy and not text.endswith("."))):
                b.role = "heading"
                if in_references and not _REFERENCES_RE.match(text):
                    in_references = False
                continue
            if in_references:
                b.role = "references"
                continue
            lines = [p.lines[i] for i in b.lines]
            ft = p.groups[lines[0].group]
            if b.size <= 0.88 * body and min(ln.fy0 for ln in lines) > 0.6 * ft.height:
                b.role = "footnote"


def _mostly_bold(page: PageLayout, block: Block) -> bool:
    total = bold = 0
    for li in block.lines:
        for ci in page.lines[li].chars:
            total += 1
            if page.chars[ci].weight >= 600:
                bold += 1
    return total > 0 and bold >= 0.7 * total
