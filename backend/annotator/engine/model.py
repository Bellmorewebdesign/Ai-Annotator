"""Core data structures shared by the extraction, matching and rendering stages.

Coordinate systems
------------------
* **User space**: native PDF coordinates (origin bottom-left, y up). Annotation
  ``/Rect`` and ``/QuadPoints`` must be written in user space.
* **Frame space**: a text-aligned frame per orientation group on a page. Text runs
  left-to-right along +x and successive lines advance along +y (y grows downward),
  so layout analysis never has to care about page ``/Rotate`` or sideways text.

Each page can contain several orientation groups (for example a landscape table on a
portrait page). Each group has its own :class:`FrameTransform`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FrameTransform:
    """Maps between PDF user space and a text-aligned frame.

    ``theta`` is the text baseline direction in user space (degrees counter-clockwise,
    one of 0, 90, 180, 270). ``box`` is the page crop box in user space.
    """

    theta: int
    box: tuple[float, float, float, float]  # x0, y0, x1, y1

    @property
    def width(self) -> float:
        x0, y0, x1, y1 = self.box
        return (x1 - x0) if self.theta in (0, 180) else (y1 - y0)

    @property
    def height(self) -> float:
        x0, y0, x1, y1 = self.box
        return (y1 - y0) if self.theta in (0, 180) else (x1 - x0)

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        t = self.theta
        if t == 0:
            return x - x0, y1 - y
        if t == 90:
            return y - y0, x - x0
        if t == 180:
            return x1 - x, y - y0
        return y1 - y, x1 - x  # 270

    def to_user(self, fx: float, fy: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        t = self.theta
        if t == 0:
            return fx + x0, y1 - fy
        if t == 90:
            return fy + x0, fx + y0
        if t == 180:
            return x1 - fx, fy + y0
        return x1 - fy, y1 - fx  # 270

    def box_to_frame(self, ux0: float, uy0: float, ux1: float, uy1: float) -> tuple[float, float, float, float]:
        ax, ay = self.to_frame(ux0, uy0)
        bx, by = self.to_frame(ux1, uy1)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


@dataclass(slots=True)
class Char:
    text: str
    # Loose glyph box in user space.
    ux0: float
    uy0: float
    ux1: float
    uy1: float
    # Same box in the frame of the char's orientation group.
    fx0: float = 0.0
    fy0: float = 0.0
    fx1: float = 0.0
    fy1: float = 0.0
    baseline: float = 0.0  # frame y of the glyph origin
    size: float = 0.0
    weight: int = 400
    space_before: bool = False
    group: int = 0
    line: int = -1  # index into PageLayout.lines once assigned
    ocr_conf: float | None = None  # word confidence (0-100) for OCR-derived chars

    @property
    def height(self) -> float:
        return self.fy1 - self.fy0


@dataclass(slots=True)
class Line:
    index: int
    group: int
    chars: list[int]  # page char indices, left to right
    fx0: float
    fy0: float
    fx1: float
    fy1: float
    size: float
    # Hyphen chars at the end of the line that were removed when joining a hyphenated
    # word across lines. They are highlighted when a selection continues past them.
    elided: list[int] = field(default_factory=list)
    slot_top: float = float("-inf")
    slot_bottom: float = float("inf")
    column: int = 0
    band: int = 0

    @property
    def height(self) -> float:
        return self.fy1 - self.fy0

    @property
    def cy(self) -> float:
        return (self.fy0 + self.fy1) / 2


BLOCK_ROLES = ("body", "heading", "caption", "footnote", "header", "footer", "references", "ocr")


@dataclass(slots=True)
class Block:
    id: str  # stable passage id, e.g. "p3.12"
    page: int  # 0-based page index
    lines: list[int]
    role: str = "body"
    text: str = ""
    size: float = 0.0


@dataclass(slots=True)
class Region:
    """A visual region (figure, table, image) that may receive a box or circle."""

    id: str  # e.g. "p3.F1"
    page: int
    kind: str  # "image" | "figure" | "table"
    user_box: tuple[float, float, float, float]  # x0, y0, x1, y1 in user space
    confidence: str  # "high" | "medium" | "low"
    caption_block: str | None = None
    caption: str = ""
    source: str = ""  # how the region was found


@dataclass(slots=True)
class PageLayout:
    index: int
    crop_box: tuple[float, float, float, float]
    rotation: int  # page /Rotate in degrees clockwise
    groups: list[FrameTransform]
    chars: list[Char]
    lines: list[Line] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)  # reading order
    regions: list[Region] = field(default_factory=list)
    skipped_chars: int = 0  # chars at non-axis-aligned angles that cannot be anchored
    image_coverage: float = 0.0  # fraction of page area covered by raster images
    unicode_errors: int = 0
    is_ocr: bool = False
    ocr_mean_conf: float | None = None
    needs_ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def display_size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.crop_box
        w, h = x1 - x0, y1 - y0
        return (h, w) if self.rotation in (90, 270) else (w, h)


@dataclass(slots=True)
class DocumentLayout:
    pages: list[PageLayout]
    body_size: float = 10.0
    title: str = ""

    def block_by_id(self) -> dict[str, Block]:
        return {b.id: b for p in self.pages for b in p.blocks}


@dataclass(frozen=True, slots=True)
class CharRef:
    page: int
    char: int
