"""Character-level text extraction with PDFium (via pypdfium2, Apache-2.0/BSD).

Produces :class:`PageLayout` objects holding every visible character with its loose
glyph box in PDF user space plus a text-aligned frame position. Layout analysis
(lines, columns, paragraphs) happens in :mod:`annotator.engine.layout`.
"""

from __future__ import annotations

import ctypes
import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .model import Char, FrameTransform, PageLayout

# Characters that never carry a visible glyph worth anchoring to.
_SKIP_TEXT = {"\r", "\n", "\x00", "￾", "￿"}
_SPACE_TEXT = {" ", " ", " ", " ", " ", "\t"}


class PdfOpenError(Exception):
    """The PDF could not be opened (corrupt, encrypted or not a PDF)."""

    def __init__(self, message: str, code: str = "pdf_unreadable"):
        super().__init__(message)
        self.code = code


def open_pdf(path: Path | str) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:  # pragma: no cover - message varies by version
        msg = str(exc).lower()
        if "password" in msg:
            raise PdfOpenError("This PDF is password-protected.", "pdf_encrypted") from exc
        raise PdfOpenError("This PDF could not be read. It may be damaged.") from exc


def _quantize_angle(a: float, b: float) -> int | None:
    """Return the text direction (0/90/180/270) of a char matrix, or None if skewed."""
    if a == 0 and b == 0:
        return 0
    deg = math.degrees(math.atan2(b, a)) % 360
    for target in (0, 90, 180, 270, 360):
        if abs(deg - target) <= 4:
            return target % 360
    return None


def extract_page_chars(page: pdfium.PdfPage, index: int) -> PageLayout:
    textpage = page.get_textpage()
    try:
        crop = tuple(float(v) for v in page.get_cropbox())
        rotation = int(page.get_rotation()) % 360
        n = textpage.count_chars()
        raw = textpage.raw

        rect = pdfium_c.FS_RECTF()
        matrix = pdfium_c.FS_MATRIX()
        ox, oy = ctypes.c_double(), ctypes.c_double()

        pending_space = False
        provisional: list[tuple[Char, int, float, float]] = []
        skipped = 0
        unicode_errors = 0
        for i in range(n):
            code = pdfium_c.FPDFText_GetUnicode(raw, i)
            text = chr(code) if 0 < code < 0x110000 else ""
            if pdfium_c.FPDFText_IsGenerated(raw, i) == 1:
                if text == " ":
                    pending_space = True
                elif text in ("\r", "\n"):
                    pending_space = True
                continue
            if text == "\x02":
                text = "-"  # PDFium reports a hyphen at the end of a line as U+0002
            if not text or text in _SKIP_TEXT:
                continue
            if text in _SPACE_TEXT or text.isspace():
                pending_space = True
                continue
            if pdfium_c.FPDFText_HasUnicodeMapError(raw, i) == 1:
                unicode_errors += 1
            if not pdfium_c.FPDFText_GetLooseCharBox(raw, i, rect):
                continue
            x0, x1 = sorted((rect.left, rect.right))
            y0, y1 = sorted((rect.bottom, rect.top))
            if x1 - x0 <= 0.01 or y1 - y0 <= 0.01:
                continue
            theta = 0
            if pdfium_c.FPDFText_GetMatrix(raw, i, matrix):
                q = _quantize_angle(matrix.a, matrix.b)
                if q is None:
                    skipped += 1
                    continue
                theta = q
            pdfium_c.FPDFText_GetCharOrigin(raw, i, ox, oy)
            size = float(pdfium_c.FPDFText_GetFontSize(raw, i) or 0.0)
            weight = int(pdfium_c.FPDFText_GetFontWeight(raw, i) or 400)
            ch = Char(text=text, ux0=x0, uy0=y0, ux1=x1, uy1=y1, size=size, weight=weight,
                      space_before=pending_space)
            provisional.append((ch, theta, ox.value, oy.value))
            pending_space = False
    finally:
        textpage.close()

    _split_ligature_boxes(provisional)

    # Orientation groups: the dominant direction first so its text reads first.
    counts = Counter(theta for _, theta, _, _ in provisional)
    order = [t for t, _ in counts.most_common()] or [0]
    groups = [FrameTransform(theta=t, box=crop) for t in order]
    group_of = {t: gi for gi, t in enumerate(order)}

    chars: list[Char] = []
    for ch, theta, gx, gy in provisional:
        gi = group_of[theta]
        ft = groups[gi]
        fx0, fy0, fx1, fy1 = ft.box_to_frame(ch.ux0, ch.uy0, ch.ux1, ch.uy1)
        _, base = ft.to_frame(gx, gy)
        if not (fy0 - 1 <= base <= fy1 + 1):
            base = fy0 + 0.78 * (fy1 - fy0)
        if ch.size <= 0.5:
            ch.size = (fy1 - fy0) / 1.15
        ch.fx0, ch.fy0, ch.fx1, ch.fy1 = fx0, fy0, fx1, fy1
        ch.baseline = base
        ch.group = gi
        chars.append(ch)

    chars = _drop_duplicate_glyphs(chars)
    layout = PageLayout(index=index, crop_box=crop, rotation=rotation, groups=groups, chars=chars,
                        skipped_chars=skipped, unicode_errors=unicode_errors)
    layout.image_coverage = _image_coverage(page, crop)
    return layout


def _split_ligature_boxes(items: list[tuple[Char, int, float, float]]) -> None:
    """PDFium expands a ligature glyph (e.g. "fi") into several chars sharing one box.

    Divide that box evenly along the text direction so each component has its own
    position; otherwise the components look like overlapping glyphs.
    """
    i = 0
    n = len(items)
    while i < n:
        c0, theta, _, _ = items[i]
        j = i + 1
        while j < n:
            cj = items[j][0]
            if (items[j][1] == theta and abs(cj.ux0 - c0.ux0) < 0.01 and abs(cj.ux1 - c0.ux1) < 0.01
                    and abs(cj.uy0 - c0.uy0) < 0.01 and abs(cj.uy1 - c0.uy1) < 0.01 and cj.text != c0.text):
                j += 1
            else:
                break
        k = j - i
        if k > 1:
            x0, y0, x1, y1 = c0.ux0, c0.uy0, c0.ux1, c0.uy1  # shared box, before any splitting
            for m in range(k):
                c = items[i + m][0]
                if theta in (0, 180):
                    w = (x1 - x0) / k
                    pos = m if theta == 0 else k - 1 - m
                    c.ux0, c.ux1 = x0 + pos * w, x0 + (pos + 1) * w
                else:
                    h = (y1 - y0) / k
                    pos = m if theta == 90 else k - 1 - m
                    c.uy0, c.uy1 = y0 + pos * h, y0 + (pos + 1) * h
        i = j


def _drop_duplicate_glyphs(chars: list[Char]) -> list[Char]:
    """Remove glyphs drawn twice at (almost) the same spot, e.g. simulated bold."""
    out: list[Char] = []
    recent: list[Char] = []
    for c in chars:
        dup = False
        for p in recent:
            if p.text == c.text and p.group == c.group:
                w = max(c.fx1 - c.fx0, 0.1)
                if abs(p.fx0 - c.fx0) < 0.3 * w and abs(p.fy0 - c.fy0) < 0.3 * (c.fy1 - c.fy0):
                    dup = True
                    break
        if not dup:
            out.append(c)
            recent.append(c)
            if len(recent) > 6:
                recent.pop(0)
    return out


def _image_coverage(page: pdfium.PdfPage, crop: tuple[float, float, float, float]) -> float:
    area = max((crop[2] - crop[0]) * (crop[3] - crop[1]), 1.0)
    covered = 0.0
    try:
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
            left, b, r, t = obj.get_bounds()
            w = max(0.0, min(r, crop[2]) - max(left, crop[0]))
            h = max(0.0, min(t, crop[3]) - max(b, crop[1]))
            covered = max(covered, w * h)
    except Exception:  # pragma: no cover - defensive against odd page objects
        return 0.0
    return min(covered / area, 1.0)


def extract_document_chars(
    path: Path | str,
    on_page: Callable[[int, int], None] | None = None,
) -> list[PageLayout]:
    pdf = open_pdf(path)
    try:
        total = len(pdf)
        pages: list[PageLayout] = []
        for i in range(total):
            page = pdf[i]
            try:
                layout = extract_page_chars(page, i)
            finally:
                page.close()
            # A page with almost no text but a large raster image is likely scanned.
            if len(layout.chars) < 25 and layout.image_coverage > 0.4:
                layout.needs_ocr = True
            elif layout.chars and layout.unicode_errors > 0.5 * len(layout.chars):
                layout.needs_ocr = True
            pages.append(layout)
            if on_page:
                on_page(i + 1, total)
        return pages
    finally:
        pdf.close()
