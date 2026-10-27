"""Document text index: reading-order text with a map back to individual glyphs.

Every character of the document text points at the glyph it came from (or at
nothing, for inserted word spaces and paragraph breaks). A *skeleton* view keeps
only letters and digits (case-folded, accents and ligatures decomposed) so quotes
can be matched robustly against text with odd spacing, hyphenation, ligatures or
typographic punctuation, while still mapping back to exact glyphs.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .layout import block_text_map
from .model import Block, CharRef, DocumentLayout

BLOCK_SEPARATOR = "\n\n"


def skeleton_chars(ch: str) -> str:
    out = []
    for o in unicodedata.normalize("NFKD", ch).casefold():
        if unicodedata.category(o).startswith("M"):
            continue
        if o.isalnum():
            out.append(o)
    return "".join(out)


def skeleton(text: str) -> str:
    return "".join(skeleton_chars(c) for c in text)


@dataclass
class DocText:
    text: str
    refs: list[CharRef | None]
    block_spans: dict[str, tuple[int, int]]  # block id -> [start, end) in text
    block_order: list[str]
    blocks: dict[str, Block]
    skel: str = ""
    skel_to_text: list[int] = field(default_factory=list)
    text_to_skel: list[int] = field(default_factory=list)  # first skel index at/after pos

    def block_index(self, block_id: str) -> int:
        return self._order_index[block_id]

    def __post_init__(self) -> None:
        self._order_index = {bid: i for i, bid in enumerate(self.block_order)}

    def skel_range_for_blocks(self, first: int, last: int) -> tuple[int, int]:
        """Skeleton [start, end) covering blocks first..last (indices in block_order)."""
        s = self.block_spans[self.block_order[first]][0]
        e = self.block_spans[self.block_order[last]][1]
        return self.text_to_skel[s], self.text_to_skel[e]

    def block_at(self, text_pos: int) -> str | None:
        # Binary search over block spans in reading order.
        lo, hi = 0, len(self.block_order) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s, e = self.block_spans[self.block_order[mid]]
            if text_pos < s:
                hi = mid - 1
            elif text_pos >= e:
                lo = mid + 1
            else:
                return self.block_order[mid]
        return None


def build_doc_text(doc: DocumentLayout) -> DocText:
    pieces: list[str] = []
    refs: list[CharRef | None] = []
    spans: dict[str, tuple[int, int]] = {}
    order: list[str] = []
    blocks: dict[str, Block] = {}
    pos = 0
    for page in doc.pages:
        for block in page.blocks:
            if order:
                pieces.append(BLOCK_SEPARATOR)
                refs.extend([None] * len(BLOCK_SEPARATOR))
                pos += len(BLOCK_SEPARATOR)
            start = pos
            for piece, ci in block_text_map(page, block):
                pieces.append(piece)
                ref = CharRef(page.index, ci) if ci >= 0 else None
                refs.extend([ref] * len(piece))
                pos += len(piece)
            spans[block.id] = (start, pos)
            order.append(block.id)
            blocks[block.id] = block
    text = "".join(pieces)
    dt = DocText(text=text, refs=refs, block_spans=spans, block_order=order, blocks=blocks)

    skel: list[str] = []
    s2t: list[int] = []
    t2s: list[int] = []
    for i, ch in enumerate(text):
        t2s.append(len(skel))
        for o in skeleton_chars(ch):
            skel.append(o)
            s2t.append(i)
    t2s.append(len(skel))
    dt.skel = "".join(skel)
    dt.skel_to_text = s2t
    dt.text_to_skel = t2s
    return dt
