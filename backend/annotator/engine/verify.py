"""Quality verification of an annotated PDF.

The check is independent of the layout code: glyphs are re-extracted from the *output*
file with PDFium, and an annotation passes only if the glyphs whose centres fall inside
its quads spell exactly the intended text. It also confirms that page content and page
boxes are unchanged (layout preserved), that every annotation stays inside the page,
and that every page renders.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .textindex import skeleton

TOL = 0.75  # points


@dataclass
class ExpectedAnnotation:
    id: str
    page: int
    kind: str
    expected_text: str | None  # None for shapes
    quads: list[tuple[float, ...]] = field(default_factory=list)


@dataclass
class VerificationReport:
    ok: bool
    page_count_ok: bool = True
    content_preserved: bool = True
    pages_rendered: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # annotation id -> reason
    checked: int = 0
    problems: list[str] = field(default_factory=list)


def _page_fingerprint(page: pikepdf.Page) -> str:
    h = hashlib.sha256()
    contents = page.obj.get("/Contents")
    if contents is not None:
        streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
        for s in streams:
            try:
                h.update(s.read_bytes())
            except Exception:  # pragma: no cover - undecodable stream: compare raw bytes
                h.update(s.read_raw_bytes())
    for key in ("/MediaBox", "/CropBox", "/Rotate"):
        value = page.obj.get(key)
        if isinstance(value, pikepdf.Array):
            value = [float(v) for v in value]
        h.update(repr(value).encode())
    return h.hexdigest()


def page_fingerprints(path: Path) -> list[str]:
    with pikepdf.open(path) as pdf:
        return [_page_fingerprint(p) for p in pdf.pages]


def _raw_chars(textpage) -> list[tuple[str, float, float, float, float]]:
    out = []
    rect = pdfium_c.FS_RECTF()
    for i in range(textpage.count_chars()):
        if pdfium_c.FPDFText_IsGenerated(textpage, i) == 1:
            continue
        code = pdfium_c.FPDFText_GetUnicode(textpage, i)
        if not code:
            continue
        ch = chr(code)
        if ch.isspace():
            continue
        if not pdfium_c.FPDFText_GetLooseCharBox(textpage, i, rect):
            continue
        x0, x1 = sorted((rect.left, rect.right))
        y0, y1 = sorted((rect.bottom, rect.top))
        # Skip glyphs drawn twice at the same spot (simulated bold), as extraction does.
        w, h = max(x1 - x0, 0.1), max(y1 - y0, 0.1)
        if any(p[0] == ch and abs(p[1] - x0) < 0.3 * w and abs(p[2] - y0) < 0.3 * h for p in out[-6:]):
            continue
        out.append((ch, x0, y0, x1, y1))
    return out


def text_under_quads(chars, quads) -> str:
    """Skeleton of glyphs whose centres lie inside the quads, read in quad order."""
    pieces = []
    for q in quads:
        xs, ys = q[0::2], q[1::2]
        bx0, bx1, by0, by1 = min(xs) - TOL, max(xs) + TOL, min(ys) - TOL, max(ys) + TOL
        dx, dy = q[2] - q[0], q[3] - q[1]  # text direction: UL -> UR
        inside = []
        for k, (ch, x0, y0, x1, y1) in enumerate(chars):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                # Ligature components share a position; keep their stream order on ties.
                inside.append((round(cx * dx + cy * dy, 3), k, ch))
        inside.sort()
        pieces.append("".join(c for _, _, c in inside))
    return skeleton("".join(pieces))


def verify_output(
    src: Path,
    out: Path,
    expected: list[ExpectedAnnotation],
    src_fingerprints: list[str] | None = None,
    render_scale: float = 0.35,
    glyph_override: dict[int, list] | None = None,
) -> VerificationReport:
    """Verify ``out``. ``glyph_override`` supplies glyphs for pages without a text layer
    (OCR pages), as (text, x0, y0, x1, y1) tuples in user space."""
    report = VerificationReport(ok=True)
    src_fp = src_fingerprints if src_fingerprints is not None else page_fingerprints(src)

    try:
        with pikepdf.open(out) as pdf:
            if len(pdf.pages) != len(src_fp):
                report.page_count_ok = False
                report.problems.append("Page count changed.")
            found: dict[str, pikepdf.Dictionary] = {}
            boxes: dict[int, tuple[float, float, float, float]] = {}
            for pi, page in enumerate(pdf.pages):
                if pi < len(src_fp) and _page_fingerprint(page) != src_fp[pi]:
                    report.content_preserved = False
                    report.problems.append(f"Page {pi + 1} content changed.")
                box = [float(v) for v in (page.obj.get("/CropBox") or page.obj.MediaBox)]
                boxes[pi] = (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))
                for a in page.obj.get("/Annots") or []:
                    nm = a.get("/NM")
                    if nm is not None:
                        found[str(nm)] = (pi, [float(v) for v in a.Rect], a)
    except Exception as exc:
        report.ok = False
        report.problems.append(f"Output could not be opened: {type(exc).__name__}")
        return report

    doc = pdfium.PdfDocument(str(out))
    try:
        chars_cache: dict[int, list] = {}
        for exp in expected:
            report.checked += 1
            hit = found.get(exp.id)
            if hit is None:
                report.failed[exp.id] = "missing"
                continue
            pi, rect, _ = hit
            if pi != exp.page:
                report.failed[exp.id] = "wrong page"
                continue
            bx0, by0, bx1, by1 = boxes[pi]
            if rect[0] < bx0 - TOL or rect[1] < by0 - TOL or rect[2] > bx1 + TOL or rect[3] > by1 + TOL:
                report.failed[exp.id] = "outside page"
                continue
            if exp.expected_text is None:
                continue
            for q in exp.quads:
                xs, ys = q[0::2], q[1::2]
                if min(xs) < rect[0] - TOL or max(xs) > rect[2] + TOL or min(ys) < rect[1] - TOL or max(ys) > rect[3] + TOL:
                    report.failed[exp.id] = "quad outside annotation rect"
                    break
            if exp.id in report.failed:
                continue
            if pi not in chars_cache and glyph_override and pi in glyph_override:
                chars_cache[pi] = glyph_override[pi]
            if pi not in chars_cache:
                page = doc[pi]
                tp = page.get_textpage()
                chars_cache[pi] = _raw_chars(tp)
                tp.close()
                page.close()
            got = text_under_quads(chars_cache[pi], exp.quads)
            want = skeleton(exp.expected_text)
            if got != want:
                report.failed[exp.id] = "text under highlight differs from selection"

        for pi in range(len(doc)):
            page = doc[pi]
            try:
                page.render(scale=render_scale, draw_annots=True).close()
                report.pages_rendered += 1
            except Exception as exc:  # pragma: no cover - renderer failure
                report.problems.append(f"Page {pi + 1} failed to render: {type(exc).__name__}")
            finally:
                page.close()
    finally:
        doc.close()

    report.ok = report.page_count_ok and report.content_preserved and not report.failed and not report.problems
    return report
