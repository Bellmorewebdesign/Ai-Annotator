"""Accuracy tests for the annotation engine: geometry and rendered pixels.

These are the most important tests in the project. Each one checks that annotations
land on exactly the intended words (compared against known word boxes) and that the
rendered output changes pixels on those words and nowhere else.
"""

from __future__ import annotations

from collections import defaultdict

import pikepdf
import pytest
from conftest import load
from engine_helpers import annotate, changed_mask, fraction, user_box_to_pixels
from fixtures import (
    Builder,
    existing_annotation_pdf,
    fonts_pdf,
    hyphen_pdf,
    long_paragraph_pdf,
    repeated_pdf,
    rotated_pdf,
    simple_pdf,
    two_column_pdf,
)

from annotator.engine.extract import PdfOpenError, open_pdf


def lines_of(truth, page=0):
    rows = defaultdict(list)
    for w in truth.words:
        if w.page == page:
            rows[round(w.baseline, 1)].append(w)
    return [rows[k] for k in sorted(rows, reverse=True)]


def word_box(words, pad_down=0.15, pad_up=0.6):
    x0 = min(w.x0 for w in words)
    x1 = max(w.x1 for w in words)
    size = words[0].size
    base = words[0].baseline
    return x0, base - pad_down * size, x1, base + pad_up * size


def quad_x_extent(q):
    xs = q[0::2]
    return min(xs), max(xs)


def assert_covers_exactly(quad, first, last, line_words):
    """The quad covers the target words and stops short of the neighbouring words.

    Glyph boxes of non-embedded standard fonts vary slightly between platforms
    (PDFium substitutes system fonts), so the edges may extend into the gap between
    words, but never onto a neighbouring word.
    """
    x0, x1 = quad_x_extent(quad)
    i, j = line_words.index(first), line_words.index(last)
    assert x0 <= first.x0 + 0.6 and x1 >= last.x1 - 0.6, "target words not fully covered"
    if i > 0:
        assert x0 > line_words[i - 1].x1, "covers the previous word"
    if j + 1 < len(line_words):
        assert x1 < line_words[j + 1].x0, "covers the next word"


def block_containing(dt, phrase):
    for bid, (s, e) in dt.block_spans.items():
        if phrase in dt.text[s:e]:
            return bid
    raise AssertionError(f"{phrase!r} not in any block")


# ----------------------------------------------------------------------------------------


def test_partial_phrase_highlights_only_those_words(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    truth = simple_pdf(src)
    res, dt = load(src)
    first_line = lines_of(truth)[0]
    target = first_line[3:6]  # three words in the middle of the first line
    phrase = " ".join(w.text for w in target)
    placement, outcome = annotate(res.doc, dt, src, out, [(phrase, block_containing(dt, phrase), "highlight")])
    assert outcome.verified and not placement.rejected
    spec = placement.specs[0]
    assert len(spec.quads) == 1
    assert_covers_exactly(spec.quads[0], target[0], target[-1], first_line)

    mask = changed_mask(src, out, 0)
    crop = res.doc.pages[0].crop_box
    assert fraction(mask, user_box_to_pixels(crop, 0, word_box(target))) > 0.45
    before, after = first_line[2], first_line[6]
    assert fraction(mask, user_box_to_pixels(crop, 0, word_box([before])), shrink=1) < 0.02
    assert fraction(mask, user_box_to_pixels(crop, 0, word_box([after])), shrink=1) < 0.02


def test_single_word_highlight(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    truth = simple_pdf(src)
    res, dt = load(src)
    word = lines_of(truth)[1][4]
    placement, outcome = annotate(res.doc, dt, src, out, [(word.text, block_containing(dt, word.text), "highlight")])
    assert outcome.verified
    quad = placement.specs[0].quads[0]
    qx0, qx1 = quad_x_extent(quad)
    # The first occurrence in the passage is used; it must cover exactly that one word.
    hit = next(w for w in truth.words if w.text == word.text and w.x0 < qx1 and w.x1 > qx0
               and abs(w.baseline - min(quad[1::2])) < 6)
    line = next(ln for ln in lines_of(truth) if hit in ln)
    assert_covers_exactly(quad, hit, hit, line)


def test_selection_across_lines_has_one_quad_per_line(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    truth = simple_pdf(src)
    res, dt = load(src)
    lines = lines_of(truth)
    target = lines[1][-3:] + lines[2][:4]
    phrase = " ".join(w.text for w in target)
    placement, outcome = annotate(res.doc, dt, src, out, [(phrase, block_containing(dt, lines[1][-1].text), "highlight")])
    assert outcome.verified and not placement.rejected
    quads = placement.specs[0].quads
    assert len(quads) == 2
    assert_covers_exactly(quads[0], lines[1][-3], lines[1][-1], lines[1])
    assert_covers_exactly(quads[1], lines[2][0], lines[2][3], lines[2])
    # Lines' highlights must not overlap vertically.
    assert min(quads[0][1::2]) >= max(quads[1][1::2]) - 0.01


def test_two_column_reading_order_and_highlights(tmp):
    src, out = tmp / "two.pdf", tmp / "out.pdf"
    truth = two_column_pdf(src)
    res, dt = load(src)
    page0 = res.doc.pages[0]
    texts = [b.text for b in page0.blocks]
    left = next(t for t in texts if t.startswith("Students often highlight"))
    assert "wastes attention" in left and "recruited" not in left
    i_left = texts.index(left)
    i_right = next(i for i, t in enumerate(texts) if t.startswith("We recruited forty"))
    assert i_left < i_right, "left column must be read before right column"
    roles = {b.role for b in page0.blocks}
    assert {"header", "footer", "heading", "footnote"} <= roles

    items = [
        ("only a few words matter", block_containing(dt, "only a few words"), "highlight"),
        ("forty students from two schools", block_containing(dt, "forty students"), "highlight"),
        ("annotate articles under time pressure", block_containing(dt, "forty students"), "underline"),
    ]
    placement, outcome = annotate(res.doc, dt, src, out, items)
    assert outcome.verified and not placement.rejected
    mask = changed_mask(src, out, 0)
    crop = page0.crop_box
    for phrase in ("only a few words matter", "forty students from two schools"):
        words = truth.find(phrase, page=0)
        by_line = defaultdict(list)
        for w in words:
            by_line[w.baseline].append(w)
        for ws in by_line.values():
            assert fraction(mask, user_box_to_pixels(crop, 0, word_box(ws))) > 0.4


def test_repeated_quotation_uses_cited_passage(tmp):
    src, out = tmp / "rep.pdf", tmp / "out.pdf"
    truth = repeated_pdf(src)
    res, dt = load(src)
    second = block_containing(dt, "Second paragraph")
    placement, outcome = annotate(res.doc, dt, src, out, [("Practice improves recall over time.", second, "highlight")])
    assert outcome.verified and not placement.rejected
    q = placement.specs[0].quads[0]
    expected = truth.find("Practice improves recall over time.", nth=1)
    assert abs(min(q[1::2]) - expected[0].baseline) < 4  # second occurrence, not the first


def test_ambiguous_quotation_without_valid_passage_is_rejected(tmp):
    src, out = tmp / "rep.pdf", tmp / "out.pdf"
    repeated_pdf(src)
    res, dt = load(src)
    placement, _ = annotate(res.doc, dt, src, out, [("Practice improves recall over time.", "p9.9", "highlight")])
    assert not placement.placed
    assert "appears 2 times" in placement.rejected[0].reason


def test_fabricated_quotation_is_never_drawn(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    simple_pdf(src)
    res, dt = load(src)
    placement, outcome = annotate(res.doc, dt, src, out, [
        ("Annotation is a sacred ritual practiced by monks", "p1.1", "highlight"),
    ])
    assert not placement.placed and not placement.specs
    with pikepdf.open(out) as pdf:
        assert "/Annots" not in pdf.pages[0].obj or len(pdf.pages[0].obj.Annots) == 0


def test_near_exact_quote_is_accepted_and_flagged(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    simple_pdf(src)
    res, dt = load(src)
    # One letter differs from the source text ("recal" vs "recall").
    quote = "Students who annotate with a question in mind recal more of the argument"
    placement, outcome = annotate(res.doc, dt, src, out, [(quote, block_containing(dt, "Students who"), "highlight")])
    assert placement.placed and placement.placed[0].match == "approximate"
    assert placement.placed[0].text.endswith("argument")
    # A paraphrase is rejected.
    placement2, _ = annotate(res.doc, dt, src, tmp / "o2.pdf", [
        ("Learners with a question in mind remember more", block_containing(dt, "Students who"), "highlight"),
    ])
    assert not placement2.placed


def test_hyphenated_word_across_lines(tmp):
    src, out = tmp / "hy.pdf", tmp / "out.pdf"
    truth = hyphen_pdf(src)
    res, dt = load(src)
    block = block_containing(dt, "important word")
    placement, outcome = annotate(res.doc, dt, src, out, [
        ("an important word broken", block, "highlight"),
        ("well-known compound", block_containing(dt, "well-known"), "highlight"),
    ])
    assert outcome.verified and len(placement.placed) == 2
    q1, q2 = placement.specs[0].quads
    hyphen_word = next(w for w in truth.words if w.text == "impor-")
    assert abs(quad_x_extent(q1)[1] - hyphen_word.x1) < 1.5  # hyphen included on the first line
    assert abs(quad_x_extent(q2)[0] - 72) < 1.5


def test_ligatures_accents_quotes_and_simulated_bold(tmp):
    src, out = tmp / "fonts.pdf", tmp / "out.pdf"
    fonts_pdf(src)
    res, dt = load(src)
    items = [
        ("cafe accents and \"curly quotes\"", block_containing(dt, "caf"), "highlight"),
        ("final figure shows efficient retrieval", block_containing(dt, "retrieval"), "highlight"),
        ("fixed width characters", block_containing(dt, "fixed width"), "underline"),
        ("Simulated bold heading", block_containing(dt, "Simulated bold"), "highlight"),
    ]
    placement, outcome = annotate(res.doc, dt, src, out, items)
    assert not placement.rejected, placement.rejected
    assert outcome.verified
    assert "Simulated bold heading text" in dt.text and "SSiimm" not in dt.text


def test_rotated_pages(tmp):
    src, out = tmp / "rot.pdf", tmp / "out.pdf"
    truth = rotated_pdf(src)
    res, dt = load(src)
    p0, p1 = res.doc.pages
    assert p0.rotation == 90 and p1.rotation == 90
    assert p1.groups[0].theta == 90  # sideways content detected
    first = lines_of(truth, 0)[0]
    phrase = " ".join(w.text for w in first[2:5])
    placement, outcome = annotate(res.doc, dt, src, out, [
        (phrase, block_containing(dt, phrase), "highlight"),
        ("second line confirms", block_containing(dt, "second line"), "highlight"),
    ])
    assert outcome.verified and not placement.rejected
    mask = changed_mask(src, out, 0)
    box = user_box_to_pixels(p0.crop_box, 90, word_box(first[2:5]))
    assert fraction(mask, box) > 0.4
    # Neighbouring word stays untouched.
    assert fraction(mask, user_box_to_pixels(p0.crop_box, 90, word_box([first[5]])), shrink=1) < 0.02
    # Sideways page: the highlight must sit on the rendered text, which reads normally.
    mask1 = changed_mask(src, out, 1)
    assert mask1.sum() > 200
    ys, xs = mask1.nonzero()
    assert (xs.max() - xs.min()) > 3 * (ys.max() - ys.min()), "highlight should be horizontal on screen"


def test_selection_spanning_a_page_break(tmp):
    src, out = tmp / "long.pdf", tmp / "out.pdf"
    long_paragraph_pdf(src)
    res, dt = load(src)
    page0_last = res.doc.pages[0].blocks[-1].text.split()[-4:]
    page1_first = res.doc.pages[1].blocks[0].text.split()[:4]
    quote = " ".join(page0_last + page1_first)
    placement, outcome = annotate(res.doc, dt, src, out, [(quote, res.doc.pages[0].blocks[-1].id, "highlight")])
    assert outcome.verified and placement.placed
    assert placement.placed[0].pages == [0, 1]
    assert len(placement.specs) == 2


def test_existing_annotations_and_content_are_preserved(tmp):
    src, out = tmp / "ex.pdf", tmp / "out.pdf"
    existing_annotation_pdf(src)
    res, dt = load(src)
    phrase = "choosing the right evidence"
    placement, outcome = annotate(res.doc, dt, src, out, [(phrase, block_containing(dt, phrase), "highlight")])
    assert outcome.verified and outcome.content_preserved
    with pikepdf.open(out) as pdf:
        subtypes = [str(a.Subtype) for a in pdf.pages[0].Annots]
        assert "/Text" in subtypes and "/Highlight" in subtypes
        hl = next(a for a in pdf.pages[0].Annots if a.Subtype == "/Highlight")
        assert "/AP" in hl and str(hl.Contents) == "note 0" and str(hl.Subj) == "Main claims"


def test_overlapping_selections_do_not_double_highlight(tmp):
    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    simple_pdf(src)
    res, dt = load(src)
    b = block_containing(dt, "Readers remember")
    placement, outcome = annotate(res.doc, dt, src, out, [
        ("Readers remember passages that are tied to a clear purpose.", b, "highlight"),
        ("passages that are tied", b, "highlight"),
        ("Students who annotate with a question in mind recall more of the argument", b, "highlight"),
    ])
    assert outcome.verified
    assert len(placement.placed) == 2
    assert any("Overlaps" in r.reason for r in placement.rejected)


def test_density_cap_limits_highlight_coverage(tmp):
    src, out = tmp / "two.pdf", tmp / "out.pdf"
    two_column_pdf(src, pages=3)
    res, dt = load(src)
    items = []
    for bid in dt.block_order:
        s, e = dt.block_spans[bid]
        words = dt.text[s:e].split()
        if dt.blocks[bid].role == "body" and len(words) > 12:
            items.append((" ".join(words[:12]), bid, "highlight"))
    placement, outcome = annotate(res.doc, dt, src, out, items, density="light")
    assert outcome.verified
    assert any("over-highlighted" in r.reason for r in placement.rejected)
    highlighted = sum(len(p.text) for p in placement.placed)
    body = sum(1 for r in dt.refs if r is not None)
    assert highlighted <= 0.08 * body + 60


def test_verification_detects_misplaced_geometry(tmp):
    from annotator.engine.render import write_annotations
    from annotator.engine.verify import verify_output

    src, out = tmp / "simple.pdf", tmp / "out.pdf"
    simple_pdf(src)
    res, dt = load(src)
    phrase = "choosing the right evidence"
    from engine_helpers import CATS

    from annotator.engine.annotate import TextSelection, place_annotations

    placement = place_annotations(res.doc, dt, [TextSelection("a", "highlight", block_containing(dt, phrase), phrase, "main")],
                                  [], CATS)
    spec = placement.specs[0]
    shifted = tuple(v + (40 if i % 2 == 0 else 0) for i, v in enumerate(spec.quads[0]))
    spec.quads = [shifted]
    write_annotations(src, out, [spec])
    report = verify_output(src, out, placement.expected)
    assert not report.ok and spec.id in report.failed


def test_corrupted_and_encrypted_pdfs_fail_cleanly(tmp):
    bad = tmp / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog >> garbage without xref")
    with pytest.raises(PdfOpenError):
        _load_pages(bad)
    src = tmp / "s.pdf"
    simple_pdf(src)
    enc = tmp / "enc.pdf"
    with pikepdf.open(src) as pdf:
        pdf.save(enc, encryption=pikepdf.Encryption(user="secret", owner="owner"))
    with pytest.raises(PdfOpenError) as info:
        _load_pages(enc)
    assert info.value.code == "pdf_encrypted"


def _load_pages(path):
    pdf = open_pdf(path)
    try:
        if len(pdf) == 0:
            raise PdfOpenError("empty")
        pdf[0].get_textpage()
    finally:
        pdf.close()


def test_footnotes_captions_and_numbered_headings(tmp):
    from fixtures import figure_table_pdf

    src, out = tmp / "two.pdf", tmp / "out.pdf"
    two_column_pdf(src)
    b = Builder(tmp / "num.pdf")
    b.line(72, 720, "3.2 Measuring Recall", font="Times-Bold", size=13)
    b.line(72, 700, "Recall was measured one week later with a free-response test.", size=11)
    b.save()
    res, dt = load(src)
    placement, outcome = annotate(res.doc, dt, src, out, [
        ("informed consent", block_containing(dt, "informed consent"), "highlight"),
    ])
    assert outcome.verified and placement.placed
    assert dt.blocks[block_containing(dt, "informed consent")].role == "footnote"

    fig = tmp / "fig.pdf"
    figure_table_pdf(fig)
    res2, dt2 = load(fig)
    bid = block_containing(dt2, "Recall scores")
    assert dt2.blocks[bid].role == "caption"
    p2, o2 = annotate(res2.doc, dt2, fig, tmp / "o2.pdf", [("Recall scores by annotation strategy", bid, "highlight")])
    assert o2.verified and p2.placed

    res3, dt3 = load(tmp / "num.pdf")
    hid = block_containing(dt3, "Measuring Recall")
    assert dt3.blocks[hid].role == "heading"
    p3, o3 = annotate(res3.doc, dt3, tmp / "num.pdf", tmp / "o3.pdf", [("3.2 Measuring Recall", hid, "highlight")])
    assert o3.verified and p3.placed and p3.placed[0].text == "3.2 Measuring Recall"
