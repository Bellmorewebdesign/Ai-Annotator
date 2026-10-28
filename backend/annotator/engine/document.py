"""Load a PDF into a fully analysed :class:`DocumentLayout` (native text or OCR)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .extract import extract_page_chars, open_pdf
from .layout import analyze_page, assign_roles, block_text
from .model import DocumentLayout
from .ocr import OcrUnavailable, layout_from_tsv, render_page_image, run_tesseract
from .regions import detect_regions

ProgressFn = Callable[[int, int], None]


@dataclass
class LoadResult:
    doc: DocumentLayout
    warnings: list[str] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    skipped_pages: list[int] = field(default_factory=list)


def load_document(
    pdf_path: Path,
    *,
    tesseract: str | None,
    ocr_lang: str = "eng",
    ocr_cache: Path | None = None,
    on_page: ProgressFn | None = None,
) -> LoadResult:
    pdf = open_pdf(pdf_path)
    warnings: list[str] = []
    ocr_pages: list[int] = []
    skipped: list[int] = []
    try:
        total = len(pdf)
        pages = []
        for i in range(total):
            pdf_page = pdf[i]
            try:
                page = extract_page_chars(pdf_page, i)
                if len(page.chars) < 25 and page.image_coverage > 0.4:
                    page.needs_ocr = True
                elif page.chars and page.unicode_errors > 0.5 * len(page.chars):
                    page.needs_ocr = True

                if page.needs_ocr:
                    if tesseract:
                        try:
                            tsv, size = _ocr_page(pdf, i, tesseract, ocr_lang, ocr_cache)
                            layout_from_tsv(page, tsv, size)
                            for b in page.blocks:
                                b.text = block_text(page, b)
                            ocr_pages.append(i)
                        except OcrUnavailable as exc:
                            page.chars, page.lines, page.blocks = [], [], []
                            skipped.append(i)
                            warnings.append(f"Page {i + 1} could not be read with OCR: {exc}")
                    else:
                        page.chars, page.lines, page.blocks = [], [], []
                        skipped.append(i)
                else:
                    analyze_page(page)
                if page.skipped_chars > 20:
                    page.warnings.append(
                        f"Page {i + 1} contains slanted text that cannot be annotated precisely."
                    )
                pages.append(page)
                if not page.is_ocr and page.blocks:
                    page.regions = []  # filled after roles are known
            finally:
                pdf_page.close()
            if on_page:
                on_page(i + 1, total)

        doc = DocumentLayout(pages=pages)
        assign_roles(doc)
        for page in pages:
            if page.is_ocr or not page.blocks:
                continue
            pdf_page = pdf[page.index]
            try:
                page.regions = detect_regions(pdf_page, page)
            finally:
                pdf_page.close()
        for page in pages:
            warnings.extend(page.warnings)
        if skipped and not tesseract:
            pages_txt = ", ".join(str(p + 1) for p in skipped[:10]) + ("..." if len(skipped) > 10 else "")
            warnings.append(
                f"Page(s) {pages_txt} look scanned, but OCR (Tesseract) is not installed, so they were not annotated."
            )
        return LoadResult(doc=doc, warnings=warnings, ocr_pages=ocr_pages, skipped_pages=skipped)
    finally:
        pdf.close()


def _ocr_page(pdf, index: int, tesseract: str, lang: str, cache: Path | None) -> tuple[str, tuple[int, int]]:
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        tsv_path = cache / f"page-{index + 1}.tsv"
        size_path = cache / f"page-{index + 1}.size"
        if tsv_path.exists() and size_path.exists():
            w, h = (int(v) for v in size_path.read_text().split())
            return tsv_path.read_text(encoding="utf-8"), (w, h)
        img_path = cache / f"page-{index + 1}.png"
    else:
        import tempfile

        img_path = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        size = render_page_image(pdf, index, img_path)
        tsv = run_tesseract(tesseract, img_path, lang)
    finally:
        img_path.unlink(missing_ok=True)
    if cache is not None:
        tsv_path.write_text(tsv, encoding="utf-8")
        size_path.write_text(f"{size[0]} {size[1]}")
    return tsv, size
