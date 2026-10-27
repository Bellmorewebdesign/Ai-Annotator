"""OCR pipeline for scanned pages and images (Tesseract CLI, Apache-2.0).

Tesseract is an optional external program. When it is missing, scanned pages are
skipped with a visible warning instead of silently producing an empty result.

OCR output is converted into the same structures as native text: each recognised
word becomes a run of synthetic glyphs spread across the word's box, and Tesseract's
block / paragraph / line structure becomes the page's lines and blocks. Word-level
highlights are therefore exact to the recognised word box; partial-word selections
are approximate. Confidence is tracked per word and per page.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import statistics
import subprocess
from pathlib import Path

import pypdfium2 as pdfium

from .model import Block, Char, FrameTransform, Line, PageLayout

OCR_DPI = 300
LOW_CONFIDENCE = 70.0
_WINDOWS_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


class OcrUnavailable(Exception):
    pass


def find_tesseract(configured: str | None = None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        for p in _WINDOWS_PATHS + [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]:
            if Path(p).exists():
                return p
    return None


def run_tesseract(cmd: str, image: Path, lang: str, timeout: int = 180) -> str:
    try:
        proc = subprocess.run(
            [cmd, str(image), "stdout", "-l", lang, "--psm", "3", "tsv"],
            capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise OcrUnavailable("Tesseract could not be started.") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailable("OCR timed out on this page.") from exc
    if proc.returncode != 0:
        raise OcrUnavailable("Tesseract failed on this page.")
    return proc.stdout.decode("utf-8", errors="replace")


def render_page_image(pdf: pdfium.PdfDocument, index: int, out: Path, dpi: int = OCR_DPI) -> tuple[int, int]:
    page = pdf[index]
    try:
        bitmap = page.render(scale=dpi / 72, draw_annots=False, grayscale=True)
        img = bitmap.to_pil()
        img.save(out)
        return img.size
    finally:
        page.close()


def layout_from_tsv(page: PageLayout, tsv: str, px_size: tuple[int, int], dpi: int = OCR_DPI) -> None:
    """Replace a page's text with OCR words. Coordinates come from the rendered (display) image."""
    scale = 72.0 / dpi
    ft = FrameTransform(theta=page.rotation, box=page.crop_box)
    page.groups = [ft]
    page.chars = []
    page.lines = []
    page.blocks = []
    page.is_ocr = True

    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    lines_by_key: dict[tuple[int, int, int], list[int]] = {}
    line_order: list[tuple[int, int, int]] = []
    confs: list[float] = []
    for row in rows:
        try:
            if int(row.get("level") or 0) != 5:
                continue
            text = (row.get("text") or "").strip()
            conf = float(row.get("conf") or -1)
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        except (KeyError, ValueError):
            continue
        if not text or conf < 0 or width <= 0 or height <= 0:
            continue
        confs.append(conf)
        if key not in lines_by_key:
            lines_by_key[key] = []
            line_order.append(key)
        x0, y0 = left * scale, top * scale
        w, h = width * scale, height * scale
        step = w / len(text)
        for k, ch in enumerate(text):
            fx0, fx1 = x0 + k * step, x0 + (k + 1) * step
            fy0, fy1 = y0, y0 + h
            ux_a, uy_a = ft.to_user(fx0, fy0)
            ux_b, uy_b = ft.to_user(fx1, fy1)
            c = Char(text=ch, ux0=min(ux_a, ux_b), uy0=min(uy_a, uy_b), ux1=max(ux_a, ux_b), uy1=max(uy_a, uy_b),
                     fx0=fx0, fy0=fy0, fx1=fx1, fy1=fy1, baseline=fy0 + 0.8 * h, size=h,
                     space_before=(k == 0 and bool(lines_by_key[key])), group=0, ocr_conf=conf)
            lines_by_key[key].append(len(page.chars))
            page.chars.append(c)

    blocks: dict[tuple[int, int], list[int]] = {}
    block_order: list[tuple[int, int]] = []
    for key in line_order:
        idxs = lines_by_key[key]
        cs = [page.chars[i] for i in idxs]
        ln = Line(index=len(page.lines), group=0, chars=idxs,
                  fx0=min(c.fx0 for c in cs), fy0=min(c.fy0 for c in cs),
                  fx1=max(c.fx1 for c in cs), fy1=max(c.fy1 for c in cs),
                  size=statistics.median(c.size for c in cs))
        for i in idxs:
            page.chars[i].line = ln.index
        page.lines.append(ln)
        bkey = key[:2]
        if bkey not in blocks:
            blocks[bkey] = []
            block_order.append(bkey)
        blocks[bkey].append(ln.index)

    for n, bkey in enumerate(block_order):
        lines = blocks[bkey]
        page.blocks.append(Block(id=f"p{page.index + 1}.{n + 1}", page=page.index, lines=lines, role="body",
                                 size=statistics.median(page.lines[i].size for i in lines)))
    page.ocr_mean_conf = statistics.mean(confs) if confs else None
    page.needs_ocr = False
    if page.ocr_mean_conf is None:
        page.warnings.append(f"No text was recognized on page {page.index + 1}.")
    elif page.ocr_mean_conf < LOW_CONFIDENCE:
        page.warnings.append(
            f"Text recognition on page {page.index + 1} is uncertain "
            f"(confidence {page.ocr_mean_conf:.0f}%). Some passages may be missed."
        )
