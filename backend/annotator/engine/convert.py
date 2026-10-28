"""Convert supported inputs into a PDF that the annotation engine can process."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageOps
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .validate import UnsupportedFile

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_WINDOWS_SOFFICE = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


@dataclass
class ConversionResult:
    pdf_path: Path
    method: str  # native | libreoffice | docx-text | text | image
    notes: list[str] = field(default_factory=list)


_fonts_registered = False


def register_fonts() -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont("DejaVuSerif", str(FONTS_DIR / "DejaVuSerif.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSerif-Bold", str(FONTS_DIR / "DejaVuSerif-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans", str(FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("DejaVuSerif", normal="DejaVuSerif", bold="DejaVuSerif-Bold",
                                  italic="DejaVuSerif", boldItalic="DejaVuSerif-Bold")
    _fonts_registered = True


def find_soffice(configured: str | None = None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for p in _WINDOWS_SOFFICE:
            if Path(p).exists():
                return p
    return None


# ----------------------------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------------------------

def _body_style() -> ParagraphStyle:
    register_fonts()
    return ParagraphStyle("body", fontName="DejaVuSerif", fontSize=11, leading=16, alignment=TA_LEFT,
                          spaceAfter=8)


def text_to_pdf(text: str, out: Path, title: str | None = None) -> None:
    register_fonts()
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch, title=title or "", author="")
    body = _body_style()
    story = []
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    for para in text.split("\n\n"):
        if not para.strip():
            continue
        html = "<br/>".join(escape(line) for line in para.split("\n"))
        story.append(Paragraph(html, body))
    if not story:
        raise UnsupportedFile("This text file has no readable content.", "empty_file")
    doc.build(story)


def txt_file_to_pdf(src: Path, out: Path, encoding: str) -> ConversionResult:
    text = src.read_bytes().decode(encoding, errors="replace")
    text_to_pdf(text, out)
    return ConversionResult(out, "text", ["Converted from plain text."])


# ----------------------------------------------------------------------------------------
# DOCX
# ----------------------------------------------------------------------------------------

def docx_to_pdf(src: Path, out: Path, soffice: str | None, timeout: int = 180) -> ConversionResult:
    if soffice:
        with tempfile.TemporaryDirectory(prefix="annotator-lo-") as tmp:
            tmpdir = Path(tmp)
            in_copy = tmpdir / "input.docx"
            shutil.copyfile(src, in_copy)
            profile = (tmpdir / "profile").resolve().as_uri()
            try:
                subprocess.run(
                    [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                     "--convert-to", "pdf", "--outdir", str(tmpdir), str(in_copy)],
                    capture_output=True, timeout=timeout, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
            produced = tmpdir / "input.pdf"
            if produced.exists() and produced.stat().st_size > 0:
                shutil.move(str(produced), out)
                return ConversionResult(out, "libreoffice", ["Converted from Word with LibreOffice."])
    # Fallback keeps the text but not the original layout. Make that visible.
    _docx_text_to_pdf(src, out)
    return ConversionResult(out, "docx-text", [
        "LibreOffice was not available, so the Word document was converted with a simplified layout. "
        "Text and paragraphs are preserved; images, tables and original formatting are not."
    ])


def _docx_text_to_pdf(src: Path, out: Path) -> None:
    import docx  # python-docx

    try:
        document = docx.Document(str(src))
    except Exception as exc:
        raise UnsupportedFile("This Word document could not be read. It may be damaged.", "corrupt_file") from exc
    register_fonts()
    body = _body_style()
    heading = ParagraphStyle("h", parent=body, fontName="DejaVuSerif-Bold", fontSize=13, leading=18,
                             spaceBefore=6)
    story = []
    for p in document.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue
        style = heading if (p.style is not None and p.style.name.lower().startswith(("heading", "title"))) else body
        story.append(Paragraph(escape(txt), style))
    for table in document.tables:
        for row in table.rows:
            cells = " | ".join(c.text.strip() for c in row.cells)
            if cells.strip(" |"):
                story.append(Paragraph(escape(cells), body))
    if not story:
        raise UnsupportedFile("This Word document has no readable text.", "empty_file")
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch)
    story.insert(0, Spacer(1, 1))
    doc.build(story)


def docx_plain_text(src: Path) -> str:
    import docx

    try:
        document = docx.Document(str(src))
    except Exception as exc:
        raise UnsupportedFile("This Word document could not be read.", "corrupt_file") from exc
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


# ----------------------------------------------------------------------------------------
# Images
# ----------------------------------------------------------------------------------------

def image_to_pdf(src: Path, out: Path) -> ConversionResult:
    try:
        with Image.open(src) as im:
            im.load()
            img = ImageOps.exif_transpose(im)
            dpi_info = im.info.get("dpi")
    except Exception as exc:
        raise UnsupportedFile("This image could not be opened. It may be damaged.", "corrupt_file") from exc
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if w < 50 or h < 50:
        raise UnsupportedFile("This image is too small to read.", "image_too_small")
    dpi = None
    if dpi_info:
        try:
            d = float(dpi_info[0])
            if 50 <= d <= 1200:
                dpi = d
        except (TypeError, ValueError, IndexError):
            dpi = None
    if dpi is None:
        dpi = max(72.0, max(w, h) / 11.0)  # longest side becomes 11 inches
    img.save(out, "PDF", resolution=dpi)
    return ConversionResult(out, "image", ["Image text was read with OCR."])
