"""Synthetic PDF fixtures with ground-truth word positions.

Every fixture is generated in code so tests are reproducible and contain no
third-party documents. Builders record the exact user-space box of every word they
draw, which lets tests compare annotation geometry with known positions.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from annotator.engine.convert import register_fonts


@dataclass
class Word:
    text: str
    page: int
    x0: float
    x1: float
    baseline: float
    size: float


@dataclass
class Truth:
    words: list[Word] = field(default_factory=list)

    def find(self, phrase: str, page: int | None = None, nth: int = 0) -> list[Word]:
        """Words of the nth occurrence of a phrase (in drawing order)."""
        target = phrase.split()
        hits = []
        for i in range(len(self.words) - len(target) + 1):
            seq = self.words[i:i + len(target)]
            if [w.text for w in seq] == target and (page is None or seq[0].page == page):
                hits.append(seq)
        return hits[nth]


class Builder:
    def __init__(self, path: Path, pagesize=LETTER):
        register_fonts()
        self.path = path
        self.c = canvas.Canvas(str(path), pagesize=pagesize)
        self.page = 0
        self.truth = Truth()

    def line(self, x: float, y: float, text: str, font: str = "Times-Roman", size: float = 10.0) -> None:
        self.c.setFont(font, size)
        self.c.drawString(x, y, text)
        cursor = x
        for k, word in enumerate(text.split(" ")):
            if k:
                cursor += pdfmetrics.stringWidth(" ", font, size)
            w = pdfmetrics.stringWidth(word, font, size)
            if word:
                self.truth.words.append(Word(word, self.page, cursor, cursor + w, y, size))
            cursor += w

    def next_page(self) -> None:
        self.c.showPage()
        self.page += 1

    def save(self) -> Truth:
        self.c.save()
        return self.truth


LOREM = (
    "Annotation quality depends on choosing the right evidence rather than the largest amount of text. "
    "Readers remember passages that are tied to a clear purpose. Students who annotate with a question "
    "in mind recall more of the argument and can locate supporting details faster during review. "
    "Highlighting entire paragraphs reduces this benefit because nothing stands out on the page."
)


def wrap(text: str, width: float, font: str, size: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def simple_pdf(path: Path) -> Truth:
    b = Builder(path)
    y = 720
    for ln in wrap(LOREM, 460, "Times-Roman", 11):
        b.line(72, y, ln, size=11)
        y -= 14
    return b.save()


def two_column_pdf(path: Path, pages: int = 2) -> Truth:
    rnd = random.Random(7)
    vocab = ("the method results evidence data students reading memory review question argument claim "
             "supports analysis effect sample control group measured significant participants trial "
             "observed outcome limited design future study suggests").split()
    b = Builder(path)
    for p in range(pages):
        b.line(72, 760, "Journal of Reading Research", font="Times-Italic", size=8)
        b.line(470, 760, f"Vol. 12 No. {p + 3}", font="Times-Italic", size=8)
        y_top = 720
        if p == 0:
            b.line(140, 720, "Precision Annotation and Student Recall", font="Times-Bold", size=16)
            abstract = ("Abstract. We test whether precise highlighting improves recall compared with "
                        "highlighting whole lines, using a controlled trial with university students.")
            y = 690
            for ln in wrap(abstract, 468, "Times-Roman", 10):
                b.line(72, y, ln, size=10)
                y -= 12
            y_top = y - 14
        for col, x in enumerate((72, 318)):
            y = y_top
            for para in range(3):
                if p == 0 and col == 0 and para == 0:
                    words = ("Students often highlight whole lines when only a few words matter for the "
                             "argument of the paper and this habit wastes attention").split()
                elif p == 0 and col == 1 and para == 1:
                    words = ("We recruited forty students from two schools and asked them to annotate "
                             "articles under time pressure before a delayed recall test").split()
                else:
                    words = [rnd.choice(vocab) for _ in range(rnd.randint(40, 55))]
                    words[0] = words[0].capitalize()
                text = " ".join(words) + "."
                lines = wrap(text, 222, "Times-Roman", 10)
                for li, ln in enumerate(lines):
                    indent = 10 if li == 0 else 0
                    b.line(x + indent, y, ln, size=10)
                    y -= 12
                y -= 6
            if col == 0:
                b.line(x, 110, "1 Participants gave informed consent before the trial began.", size=7.5)
        b.line(300, 50, str(p + 1), size=9)
        b.next_page()
    return b.save()


def rotated_pdf(path: Path) -> Truth:
    """Page 1: /Rotate 90 with normal content. Page 2: sideways content shown upright via /Rotate 270."""
    b = Builder(path)
    # reportlab swaps the media box to 792 x 612 when the page is rotated by 90 degrees.
    b.c.setPageRotation(90)
    y = 540
    for ln in wrap(LOREM, 440, "Helvetica", 10):
        b.line(72, y, ln, font="Helvetica", size=10)
        y -= 13
    b.next_page()
    # Landscape content: draw text rotated 90 degrees counter-clockwise, then rotate the page
    # 90 degrees clockwise so it reads normally on screen.
    b.c.setPageRotation(90)
    b.c.saveState()
    b.c.translate(560, 72)
    b.c.rotate(90)
    b.c.setFont("Helvetica", 10)
    sideways = ["Sideways table text reads across the landscape page.", "A second line confirms the order."]
    for i, ln in enumerate(sideways):
        b.c.drawString(0, -14 * i, ln)
    b.c.restoreState()
    b.next_page()
    return b.save()


def fonts_pdf(path: Path) -> Truth:
    b = Builder(path)
    b.line(72, 720, "Embedded serif text with café accents and “curly quotes” inside.",
           font="DejaVuSerif", size=11)
    b.line(72, 700, "The ﬁnal ﬁgure shows efﬁcient retrieval in sans serif type.",
           font="DejaVuSans", size=11)
    b.line(72, 680, "Courier text keeps fixed width characters for code samples.", font="Courier", size=10)
    # Simulated bold: the same text drawn twice with a tiny offset.
    b.line(72, 660, "Simulated bold heading text", font="Times-Roman", size=12)
    b.c.setFont("Times-Roman", 12)
    b.c.drawString(72.3, 660, "Simulated bold heading text")
    return b.save()


def hyphen_pdf(path: Path) -> Truth:
    b = Builder(path)
    b.line(72, 720, "This sentence demonstrates an impor-", size=11)
    b.line(72, 706, "tant word broken across two lines of text.", size=11)
    b.line(72, 692, "A well-known compound keeps its hyphen in the middle.", size=11)
    return b.save()


def repeated_pdf(path: Path) -> Truth:
    b = Builder(path)
    b.line(72, 720, "First paragraph. Practice improves recall over time.", size=11)
    b.line(72, 706, "It is mentioned here as background only.", size=11)
    b.line(72, 670, "Second paragraph. Practice improves recall over time.", size=11)
    b.line(72, 656, "This one is the key finding of the study.", size=11)
    return b.save()


def figure_table_pdf(path: Path) -> Truth:
    b = Builder(path)
    b.line(72, 740, "Results are summarized in the figure and table below.", size=11)
    img = Image.new("RGB", (400, 240), "white")
    d = ImageDraw.Draw(img)
    for i, h in enumerate((60, 140, 100, 190)):
        d.rectangle([40 + i * 85, 230 - h, 100 + i * 85, 230], fill=(70, 110, 170))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    from reportlab.lib.utils import ImageReader

    b.c.drawImage(ImageReader(buf), 72, 470, width=300, height=180)
    b.line(72, 452, "Figure 1: Recall scores by annotation strategy.", size=9)
    b.line(72, 400, "Table 1: Mean recall by group.", size=9)
    data = [["Group", "Recall", "N"], ["Precise", "0.71", "20"], ["Whole line", "0.58", "20"], ["None", "0.52", "20"]]
    t = Table(data, colWidths=[110, 80, 60])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1, (0, 0, 0)), ("LINEBELOW", (0, 0), (-1, 0), 0.5, (0, 0, 0)),
                           ("LINEBELOW", (0, -1), (-1, -1), 1, (0, 0, 0)), ("FONTNAME", (0, 0), (-1, -1), "Times-Roman")]))
    w, h = t.wrapOn(b.c, 400, 200)
    t.drawOn(b.c, 72, 385 - h)
    b.line(72, 260, "The precise group recalled the most information overall.", size=11)
    return b.save()


def long_paragraph_pdf(path: Path) -> Truth:
    """A paragraph long enough to continue onto a second page."""
    b = Builder(path)
    sentences = [f"Sentence number {i} adds another observation about careful reading." for i in range(1, 80)]
    text = " ".join(sentences)
    lines = wrap(text, 460, "Times-Roman", 11)
    y = 720
    for ln in lines:
        if y < 80:
            b.next_page()
            y = 720
        b.line(72, y, ln, size=11)
        y -= 14
    return b.save()


def scanned_pdf(path: Path, source: Path, dpi: int = 200) -> None:
    """Rasterise the first page of ``source`` into an image-only PDF (no text layer)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(source))
    img = pdf[0].render(scale=dpi / 72).to_pil().convert("L")
    pdf.close()
    img.save(path, "PDF", resolution=dpi)


def existing_annotation_pdf(path: Path) -> Truth:
    """A PDF that already contains a user's annotation, which must be preserved."""
    truth = simple_pdf(path)
    import pikepdf

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        note = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text,
                                                    Rect=[20, 700, 40, 720], Contents=pikepdf.String("Mine")))
        page.obj.Annots = pikepdf.Array([note])
        pdf.save(path)
    return truth


# Long enough to wrap onto a second, shorter line on every platform.
ACADEMIC_TITLE = ("Retrieval Practice and Long-Term Retention in Secondary Classrooms: "
                  "A Semester-Long Field Experiment")

ACADEMIC_PARAGRAPHS = [
    "Introduction",
    "Decades of laboratory research show that retrieving information from memory strengthens later recall "
    "more than restudying the same material. Classroom evidence, however, remains comparatively thin, and "
    "teachers frequently report that quizzes consume instructional time without clear benefits.",
    "We therefore conducted a semester-long field experiment. Students completed brief weekly quizzes on half "
    "of the course units, while the remaining units were reviewed through guided rereading of summaries.",
    "Methods",
    "Participants were 312 students aged fourteen to sixteen. Each class was randomly assigned a counterbalanced "
    "order of units, which ensured that every unit appeared in both conditions across the sample.",
    "Results",
    "Students recalled 64 percent of quizzed material compared with 47 percent of reread material, a difference "
    "that was statistically significant and consistent across schools. The effect was largest for students "
    "with lower prior achievement.",
    "Discussion",
    "These results suggest that retrieval practice is a practical and inexpensive intervention. Nevertheless, "
    "the study has limitations: quizzes were not individually adapted, and the eight-week interval cannot speak "
    "to retention over longer periods.",
    "Counterarguments deserve attention. Some researchers argue that the testing effect reflects increased "
    "exposure rather than retrieval itself; our design equated exposure time, which weakens that interpretation.",
]


def academic_docx(path: Path) -> None:
    """A Word document with a full-width title/abstract, then two justified, hyphenated columns."""
    import docx
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = docx.Document()
    d.add_heading(ACADEMIC_TITLE, level=1)
    d.add_paragraph("Abstract. This study examines whether low-stakes retrieval practice improves long-term "
                    "retention compared with rereading, in a sample of 312 secondary students.")
    sec = d.add_section(WD_SECTION.CONTINUOUS)
    cols = sec._sectPr.xpath("./w:cols")
    col = cols[0] if cols else OxmlElement("w:cols")
    col.set(qn("w:num"), "2")
    col.set(qn("w:space"), "360")
    if not cols:
        sec._sectPr.append(col)
    hyph = OxmlElement("w:autoHyphenation")
    hyph.set(qn("w:val"), "true")
    d.settings.element.append(hyph)
    for t in ACADEMIC_PARAGRAPHS:
        if len(t) < 20:
            d.add_heading(t, level=2)
        else:
            d.add_paragraph(t).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    d.save(str(path))
