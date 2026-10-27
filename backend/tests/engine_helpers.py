"""Helpers for engine accuracy tests: annotate phrases and inspect rendered pixels."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium

from annotator.engine.annotate import Category, TextSelection, place_annotations, render_and_verify
from annotator.engine.model import FrameTransform

CATS = [Category("main", "Main claims", "#FFE066"), Category("evidence", "Evidence", "#8CE99A")]
SCALE = 2.0  # 144 dpi


def annotate(doc, dt, src: Path, out: Path, items, density="detailed"):
    """items: list of (quote, passage_id, kind) tuples."""
    sels = [TextSelection(id=f"s{i}", kind=k, passage_id=pid, quote=q, category_id="main", comment=f"note {i}")
            for i, (q, pid, k) in enumerate(items)]
    placement = place_annotations(doc, dt, sels, [], CATS, density=density)
    outcome = render_and_verify(src, out, placement, doc=doc)
    return placement, outcome


def render(path: Path, page: int) -> np.ndarray:
    pdf = pdfium.PdfDocument(str(path))
    try:
        img = pdf[page].render(scale=SCALE, draw_annots=True).to_numpy()
    finally:
        pdf.close()
    return img[..., :3].astype(int)


def changed_mask(src: Path, out: Path, page: int) -> np.ndarray:
    a = render(src, page)
    b = render(out, page)
    return (np.abs(a - b).max(axis=2) > 18)


def user_box_to_pixels(page_crop, rotation: int, box) -> tuple[int, int, int, int]:
    """Map a user-space box to rendered pixel coordinates (rotation applied like viewers do)."""
    ft = FrameTransform(theta=rotation, box=page_crop)
    x0, y0, x1, y1 = ft.box_to_frame(*box)
    return int(round(x0 * SCALE)), int(round(y0 * SCALE)), int(round(x1 * SCALE)), int(round(y1 * SCALE))


def fraction(mask: np.ndarray, px_box, shrink: int = 2) -> float:
    x0, y0, x1, y1 = px_box
    region = mask[max(0, y0 + shrink):max(0, y1 - shrink), max(0, x0 + shrink):max(0, x1 - shrink)]
    return float(region.mean()) if region.size else 0.0
