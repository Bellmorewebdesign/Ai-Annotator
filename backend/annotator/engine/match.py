"""Locate AI-selected quotations in the document text.

The AI returns a passage id plus a verbatim quotation. Matching is strict by design:

1. Exact (skeleton) match inside the cited passage, honouring word boundaries.
2. Exact match starting in the cited passage and continuing into the next passages
   (sentences broken across columns or pages).
3. Exact match elsewhere on the same page, then anywhere in the document, but only
   when the location is unambiguous.
4. Near-exact fuzzy match inside the cited passage, accepted only above a high
   similarity threshold and snapped to whole words. These are flagged.

Anything else is reported as unmatched and never rendered.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from .textindex import DocText, skeleton

MIN_SKELETON_CHARS = 2


@dataclass
class MatchResult:
    status: str  # exact | relocated | approximate | ambiguous | not_found | invalid
    start: int = -1  # text position (inclusive)
    end: int = -1  # text position (inclusive)
    score: float = 0.0
    block_id: str | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("exact", "relocated", "approximate")


def _find_all(hay: str, needle: str, lo: int, hi: int) -> list[int]:
    out = []
    i = hay.find(needle, lo, hi)
    while i != -1:
        out.append(i)
        i = hay.find(needle, i + 1, hi)
    return out


def _on_word_boundaries(dt: DocText, s: int, e: int) -> bool:
    """Skeleton match [s, e) must start and end on word boundaries in the text."""
    ts = dt.skel_to_text[s]
    te = dt.skel_to_text[e - 1]
    if s > 0 and dt.skel_to_text[s - 1] == ts:
        return False  # starts inside a ligature
    if e < len(dt.skel) and dt.skel_to_text[e] == te:
        return False
    if ts > 0 and _is_word_char(dt.text[ts - 1]):
        return False
    if te + 1 < len(dt.text) and _is_word_char(dt.text[te + 1]):
        return False
    return True


def _is_word_char(c: str) -> bool:
    if c.isalnum():
        return True
    return unicodedata.category(c).startswith("M")


def _edge_punctuation(quote: str) -> tuple[int, int]:
    q = quote.strip()
    lead = 0
    while lead < len(q) and not q[lead].isalnum() and not q[lead].isspace():
        lead += 1
    trail = 0
    while trail < len(q) - lead and not q[-1 - trail].isalnum() and not q[-1 - trail].isspace():
        trail += 1
    return lead, trail


def _to_text_span(dt: DocText, s: int, e: int, quote: str) -> tuple[int, int]:
    ts = dt.skel_to_text[s]
    te = dt.skel_to_text[e - 1]
    lead, trail = _edge_punctuation(quote)
    for _ in range(trail):
        nxt = te + 1
        if nxt < len(dt.text) and not dt.text[nxt].isspace() and not dt.text[nxt].isalnum() and dt.refs[nxt]:
            te = nxt
        else:
            break
    for _ in range(lead):
        prv = ts - 1
        if prv >= 0 and not dt.text[prv].isspace() and not dt.text[prv].isalnum() and dt.refs[prv]:
            ts = prv
        else:
            break
    return ts, te


def _candidates(dt: DocText, q: str, lo: int, hi: int) -> list[int]:
    return [s for s in _find_all(dt.skel, q, lo, hi) if _on_word_boundaries(dt, s, s + len(q))]


def match_quote(
    dt: DocText,
    quote: str,
    block_id: str | None,
    occurrence: int = 1,
    fuzzy_threshold: float = 95.0,
) -> MatchResult:
    q = skeleton(quote or "")
    if len(q) < MIN_SKELETON_CHARS:
        return MatchResult("invalid", note="Quotation is empty or too short to anchor.")
    occurrence = max(1, occurrence or 1)

    bidx = dt._order_index.get(block_id) if block_id else None

    if bidx is not None:
        ps, pe = dt.skel_range_for_blocks(bidx, bidx)
        hits = _candidates(dt, q, ps, pe)
        if hits:
            s = hits[min(occurrence, len(hits)) - 1]
            note = "" if occurrence <= len(hits) else "Requested occurrence not found; used the last one."
            ts, te = _to_text_span(dt, s, s + len(q), quote)
            return MatchResult("exact", ts, te, 100.0, block_id, note)
        # Continue into following passages (sentence split by column or page break).
        last = min(bidx + 3, len(dt.block_order) - 1)
        _, ext_end = dt.skel_range_for_blocks(bidx, last)
        hits = [s for s in _candidates(dt, q, ps, ext_end) if s < pe]
        if hits:
            s = hits[0]
            ts, te = _to_text_span(dt, s, s + len(q), quote)
            return MatchResult("exact", ts, te, 100.0, block_id, "Spans multiple passages.")
        # Same page elsewhere (the model cited a neighbouring passage id).
        page = dt.blocks[block_id].page
        page_ids = [i for i, b in enumerate(dt.block_order) if dt.blocks[b].page == page]
        ps2, pe2 = dt.skel_range_for_blocks(page_ids[0], page_ids[-1])
        hits = _candidates(dt, q, ps2, pe2)
        if len(hits) == 1:
            ts, te = _to_text_span(dt, hits[0], hits[0] + len(q), quote)
            return MatchResult("relocated", ts, te, 100.0, dt.block_at(ts), "Found in a different passage on the same page.")

    hits = _candidates(dt, q, 0, len(dt.skel))
    if len(hits) == 1:
        ts, te = _to_text_span(dt, hits[0], hits[0] + len(q), quote)
        return MatchResult("relocated", ts, te, 100.0, dt.block_at(ts), "Found in a different passage.")
    if len(hits) > 1:
        return MatchResult("ambiguous", note=f"Quotation appears {len(hits)} times and the cited passage does not contain it.")

    if bidx is not None and len(q) >= 12:
        last = min(bidx + 1, len(dt.block_order) - 1)
        ps, pe = dt.skel_range_for_blocks(bidx, last)
        hay = dt.skel[ps:pe]
        if hay:
            al = fuzz.partial_ratio_alignment(q, hay, score_cutoff=fuzzy_threshold)
            if al is not None and al.score >= fuzzy_threshold:
                s, e = ps + al.dest_start, ps + al.dest_end
                s, e = _snap_to_words(dt, s, e)
                ratio = (e - s) / len(q)
                if 0.85 <= ratio <= 1.2:
                    ts, te = _to_text_span(dt, s, e, quote)
                    return MatchResult("approximate", ts, te, float(al.score), block_id,
                                       "Matched with minor differences from the quotation.")
    return MatchResult("not_found", note="Quotation not found in the document text.")


def _snap_to_words(dt: DocText, s: int, e: int) -> tuple[int, int]:
    """Expand a skeleton span [s, e) so it starts and ends on whole words."""
    # Skeleton characters belong to the same word when their text positions are adjacent.
    while s > 0 and dt.skel_to_text[s] - dt.skel_to_text[s - 1] <= 1:
        s -= 1
    n = len(dt.skel)
    while e < n and dt.skel_to_text[e] - dt.skel_to_text[e - 1] <= 1:
        e += 1
    return s, e
