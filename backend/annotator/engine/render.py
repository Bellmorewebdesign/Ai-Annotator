"""Write real PDF annotations with pikepdf (MPL-2.0, built on qpdf).

Page content streams are never modified: annotations are appended to each page's
``/Annots`` array, so the original layout is preserved byte for byte. Every
annotation carries an explicit appearance stream (``/AP``) so that viewers render it
identically instead of guessing; highlights use the Multiply blend mode so the text
underneath stays crisp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from .geometry import Quad

AUTHOR = "AI Annotator"
HIGHLIGHT_ALPHA = 0.55
KAPPA = 0.5522847498


@dataclass
class AnnotationSpec:
    id: str
    kind: str  # highlight | underline | box | circle
    page: int
    color: tuple[float, float, float]
    category: str = ""
    comment: str = ""
    quads: list[Quad] = field(default_factory=list)
    underlines: list[tuple[float, float, float, float, float]] = field(default_factory=list)  # x0,y0,x1,y1,width
    rect: tuple[float, float, float, float] | None = None  # box / circle region in user space


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return int(v[0:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255


def stroke_color(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Darker variant of a (usually pastel) category colour for outlines and underlines."""
    return tuple(max(0.0, c * 0.62) for c in rgb)  # type: ignore[return-value]


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _pdf_date(dt: datetime) -> str:
    return dt.strftime("D:%Y%m%d%H%M%SZ")


def _bbox_of_quads(quads: list[Quad]) -> tuple[float, float, float, float]:
    xs = [v for q in quads for v in q[0::2]]
    ys = [v for q in quads for v in q[1::2]]
    return min(xs), min(ys), max(xs), max(ys)


def _form(pdf: pikepdf.Pdf, bbox: tuple[float, float, float, float], content: str,
          resources: Dictionary | None = None) -> pikepdf.Stream:
    stream = pikepdf.Stream(pdf, content.encode("latin-1"))
    stream.Type = Name.XObject
    stream.Subtype = Name.Form
    stream.BBox = Array([float(v) for v in bbox])
    stream.Resources = resources if resources is not None else Dictionary()
    return stream


def _base_annot(spec: AnnotationSpec, page_obj: pikepdf.Object, rect, now: str) -> Dictionary:
    d = Dictionary(
        Type=Name.Annot,
        Rect=Array([float(v) for v in rect]),
        F=4,  # print
        NM=String(spec.id),
        T=String(AUTHOR),
        M=String(now),
        CreationDate=String(now),
        P=page_obj,
    )
    if spec.comment:
        d.Contents = String(spec.comment)
    if spec.category:
        d.Subj = String(spec.category)
    return d


def _highlight(pdf: pikepdf.Pdf, spec: AnnotationSpec, page_obj, now: str) -> Dictionary:
    rect = _bbox_of_quads(spec.quads)
    r, g, b = spec.color
    gs = Dictionary(Type=Name.ExtGState, BM=Name.Multiply, ca=HIGHLIGHT_ALPHA, CA=HIGHLIGHT_ALPHA)
    res = Dictionary(ExtGState=Dictionary(GS0=gs))
    ops = ["/GS0 gs", f"{_fmt(r)} {_fmt(g)} {_fmt(b)} rg"]
    for q in spec.quads:
        ulx, uly, urx, ury, llx, lly, lrx, lry = q
        ops.append(f"{_fmt(ulx)} {_fmt(uly)} m {_fmt(urx)} {_fmt(ury)} l {_fmt(lrx)} {_fmt(lry)} l "
                   f"{_fmt(llx)} {_fmt(lly)} l h")
    ops.append("f")
    d = _base_annot(spec, page_obj, rect, now)
    d.Subtype = Name.Highlight
    d.QuadPoints = Array([float(v) for q in spec.quads for v in q])
    d.C = Array([r, g, b])
    d.CA = HIGHLIGHT_ALPHA
    d.AP = Dictionary(N=_form(pdf, rect, "q\n" + "\n".join(ops) + "\nQ", res))
    return d


def _underline(pdf: pikepdf.Pdf, spec: AnnotationSpec, page_obj, now: str) -> Dictionary:
    r, g, b = stroke_color(spec.color)
    qx0, qy0, qx1, qy1 = _bbox_of_quads(spec.quads)
    pad = max((u[4] for u in spec.underlines), default=1.0)
    xs = [qx0, qx1] + [v for u in spec.underlines for v in (u[0], u[2])]
    ys = [qy0, qy1] + [v for u in spec.underlines for v in (u[1], u[3])]
    rect = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    ops = [f"{_fmt(r)} {_fmt(g)} {_fmt(b)} RG", "0 J"]
    for x0, y0, x1, y1, w in spec.underlines:
        ops.append(f"{_fmt(w)} w {_fmt(x0)} {_fmt(y0)} m {_fmt(x1)} {_fmt(y1)} l S")
    d = _base_annot(spec, page_obj, rect, now)
    d.Subtype = Name.Underline
    d.QuadPoints = Array([float(v) for q in spec.quads for v in q])
    d.C = Array([r, g, b])
    d.AP = Dictionary(N=_form(pdf, rect, "q\n" + "\n".join(ops) + "\nQ"))
    return d


def _shape(pdf: pikepdf.Pdf, spec: AnnotationSpec, page_obj, now: str) -> Dictionary:
    assert spec.rect is not None
    w = 1.6
    r, g, b = stroke_color(spec.color)
    x0, y0, x1, y1 = spec.rect
    rect = (x0 - w, y0 - w, x1 + w, y1 + w)
    ops = [f"{_fmt(r)} {_fmt(g)} {_fmt(b)} RG", f"{_fmt(w)} w"]
    if spec.kind == "box":
        ops.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re S")
    else:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
        kx, ky = rx * KAPPA, ry * KAPPA
        ops.append(
            f"{_fmt(cx + rx)} {_fmt(cy)} m "
            f"{_fmt(cx + rx)} {_fmt(cy + ky)} {_fmt(cx + kx)} {_fmt(cy + ry)} {_fmt(cx)} {_fmt(cy + ry)} c "
            f"{_fmt(cx - kx)} {_fmt(cy + ry)} {_fmt(cx - rx)} {_fmt(cy + ky)} {_fmt(cx - rx)} {_fmt(cy)} c "
            f"{_fmt(cx - rx)} {_fmt(cy - ky)} {_fmt(cx - kx)} {_fmt(cy - ry)} {_fmt(cx)} {_fmt(cy - ry)} c "
            f"{_fmt(cx + kx)} {_fmt(cy - ry)} {_fmt(cx + rx)} {_fmt(cy - ky)} {_fmt(cx + rx)} {_fmt(cy)} c S"
        )
    d = _base_annot(spec, page_obj, rect, now)
    d.Subtype = Name.Square if spec.kind == "box" else Name.Circle
    d.C = Array([r, g, b])
    d.BS = Dictionary(W=w, S=Name.S)
    d.RD = Array([w, w, w, w])
    d.AP = Dictionary(N=_form(pdf, rect, "q\n" + "\n".join(ops) + "\nQ"))
    return d


_BUILDERS = {"highlight": _highlight, "underline": _underline, "box": _shape, "circle": _shape}


def write_annotations(src: Path, dst: Path, specs: list[AnnotationSpec]) -> None:
    now = _pdf_date(datetime.now(UTC))
    with pikepdf.open(src) as pdf:
        by_page: dict[int, list[AnnotationSpec]] = {}
        for s in specs:
            by_page.setdefault(s.page, []).append(s)
        for page_index, page_specs in by_page.items():
            page = pdf.pages[page_index]
            page_obj = page.obj
            annots = page_obj.get("/Annots")
            if annots is None:
                annots = Array()
                page_obj.Annots = annots
            elif not isinstance(annots, Array):
                annots = Array()
                page_obj.Annots = annots
            for spec in page_specs:
                builder = _BUILDERS[spec.kind]
                annot = pdf.make_indirect(builder(pdf, spec, page_obj, now))
                page_obj.Annots.append(annot)
        pdf.save(dst)
