"""Detect visual regions (figures, images, tables) that may receive box/circle annotations.

Confidence reflects how the geometry was obtained, not a guess:

* ``high``: an embedded raster image object; its bounds come straight from the PDF.
* ``medium``: a cluster of vector drawing objects, or a table delimited by horizontal rules.
* ``low``: only a caption was found. Low-confidence regions are never boxed; the
  caption is highlighted instead and the region is flagged.
"""

from __future__ import annotations

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .model import Block, PageLayout, Region

Box = tuple[float, float, float, float]


def block_user_box(page: PageLayout, block: Block) -> Box:
    lines = [page.lines[i] for i in block.lines]
    ft = page.groups[lines[0].group]
    xs: list[float] = []
    ys: list[float] = []
    for ln in lines:
        for fx, fy in ((ln.fx0, ln.fy0), (ln.fx1, ln.fy1)):
            x, y = ft.to_user(fx, fy)
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(a: Box, b: Box) -> Box:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _near(a: Box, b: Box, gap: float) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def _clip(b: Box, crop: Box) -> Box:
    return max(b[0], crop[0]), max(b[1], crop[1]), min(b[2], crop[2]), min(b[3], crop[3])


def _cluster(boxes: list[Box], gap: float) -> list[tuple[Box, int]]:
    clusters: list[tuple[Box, int]] = []
    for b in sorted(boxes, key=lambda b: (-b[3], b[0])):
        merged = False
        for i, (cb, n) in enumerate(clusters):
            if _near(cb, b, gap):
                clusters[i] = (_union(cb, b), n + 1)
                merged = True
                break
        if not merged:
            clusters.append((b, 1))
    # A second pass merges clusters that became adjacent.
    changed = True
    while changed:
        changed = False
        out: list[tuple[Box, int]] = []
        for b, n in clusters:
            for i, (ob, on) in enumerate(out):
                if _near(ob, b, gap):
                    out[i] = (_union(ob, b), on + n)
                    changed = True
                    break
            else:
                out.append((b, n))
        clusters = out
    return clusters


def detect_regions(pdf_page: pdfium.PdfPage, page: PageLayout) -> list[Region]:
    crop: Box = page.crop_box
    page_area = max(_area(crop), 1.0)
    images: list[Box] = []
    graphics: list[Box] = []
    hrules: list[Box] = []
    try:
        objs = list(pdf_page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_PATH],
                                          max_depth=4))
    except Exception:  # pragma: no cover
        objs = []
    for obj in objs:
        try:
            b = _clip(tuple(float(v) for v in obj.get_bounds()), crop)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover
            continue
        w, h = b[2] - b[0], b[3] - b[1]
        if w <= 0 and h <= 0:
            continue
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            if w <= 0 or h <= 0:
                continue
            if _area(b) >= 0.012 * page_area and min(w, h) >= 30 and _area(b) <= 0.85 * page_area:
                images.append(b)
        else:
            if h <= 2.5 and w >= 40:
                hrules.append(b)
            elif w <= 2.5 and h >= 40:
                continue  # vertical rules belong to tables; handled through horizontal rules
            elif _area(b) >= 4 and _area(b) <= 0.7 * page_area:
                graphics.append(b)

    regions: list[tuple[str, Box, str, str]] = []  # kind, box, confidence, source
    for b, _ in _cluster(images, gap=4):
        regions.append(("image", b, "high", "embedded image"))

    for b, n in _cluster(graphics, gap=10):
        if any(_near(b, r[1], 2) for r in regions):
            continue
        if n >= 3 and _area(b) >= 0.02 * page_area and _area(b) <= 0.7 * page_area:
            regions.append(("figure", b, "medium", "vector graphics"))

    # Tables: three or more horizontal rules with similar horizontal extent.
    used: set[int] = set()
    rules = sorted(hrules, key=lambda b: -b[3])
    for i, r in enumerate(rules):
        if i in used:
            continue
        group = [i]
        for j in range(i + 1, len(rules)):
            s = rules[j]
            overlap = min(r[2], s[2]) - max(r[0], s[0])
            if overlap >= 0.8 * min(r[2] - r[0], s[2] - s[0]) and r[3] - s[3] < 450:
                group.append(j)
        if len(group) >= 3:
            used.update(group)
            box = rules[group[0]]
            for k in group[1:]:
                box = _union(box, rules[k])
            if box[3] - box[1] >= 20:
                regions = [x for x in regions if not (x[0] == "figure" and _near(x[1], box, 0))]
                regions.append(("table", box, "medium", "ruled table"))

    # Attach captions.
    captions = [(b, block_user_box(page, b)) for b in page.blocks if b.role == "caption"]
    out: list[Region] = []
    used_caps: set[str] = set()
    counters = {"F": 0, "T": 0}
    for kind, box, conf, source in sorted(regions, key=lambda r: (-r[1][3], r[1][0])):
        cap = _nearest_caption(box, captions, prefer_table=kind == "table")
        letter = "T" if kind == "table" else "F"
        counters[letter] += 1
        reg = Region(id=f"p{page.index + 1}.{letter}{counters[letter]}", page=page.index, kind=kind,
                     user_box=box, confidence=conf, source=source)
        if cap is not None:
            reg.caption_block = cap.id
            reg.caption = cap.text[:300]
            used_caps.add(cap.id)
        out.append(reg)
    for blk, cbox in captions:
        if blk.id in used_caps:
            continue
        kind = "table" if blk.text.strip().lower().startswith("table") else "figure"
        letter = "T" if kind == "table" else "F"
        counters[letter] += 1
        out.append(Region(id=f"p{page.index + 1}.{letter}{counters[letter]}", page=page.index, kind=kind,
                          user_box=cbox, confidence="low", caption_block=blk.id, caption=blk.text[:300],
                          source="caption only"))
    return out


def _nearest_caption(box: Box, captions: list[tuple[Block, Box]], prefer_table: bool) -> Block | None:
    best: tuple[float, Block] | None = None
    for blk, cb in captions:
        is_table = blk.text.strip().lower().startswith("table")
        if is_table != prefer_table:
            continue
        overlap = min(box[2], cb[2]) - max(box[0], cb[0])
        if overlap < 0.3 * min(box[2] - box[0], cb[2] - cb[0]):
            continue
        if cb[3] <= box[1]:
            dist = box[1] - cb[3]  # caption below
        elif cb[1] >= box[3]:
            dist = cb[1] - box[3]  # caption above
        else:
            dist = 0.0
        if dist <= 72 and (best is None or dist < best[0]):
            best = (dist, blk)
    return best[1] if best else None
