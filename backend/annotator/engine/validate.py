"""File type detection by content (magic bytes), never by extension alone."""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"}
KIND_LABELS = {"pdf": "PDF", "docx": "Word document", "txt": "Text file", "png": "PNG image", "jpg": "JPEG image"}


class UnsupportedFile(Exception):
    def __init__(self, message: str, code: str = "unsupported_file"):
        super().__init__(message)
        self.code = code


@dataclass
class SniffResult:
    kind: str  # pdf | docx | txt | png | jpg
    text_encoding: str | None = None


def sniff(path: Path) -> SniffResult:
    with open(path, "rb") as f:
        head = f.read(8192)
    if not head:
        raise UnsupportedFile("This file is empty.", "empty_file")
    if b"%PDF-" in head[:1024]:
        return SniffResult("pdf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return SniffResult("png")
    if head.startswith(b"\xff\xd8\xff"):
        return SniffResult("jpg")
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFile("Older .doc files are not supported. Save it as .docx or PDF and try again.")
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise UnsupportedFile("This file looks damaged and could not be opened.", "corrupt_file") from exc
        if "word/document.xml" in names:
            return SniffResult("docx")
        if any(n.startswith("ppt/") for n in names) or any(n.startswith("xl/") for n in names):
            raise UnsupportedFile("Slides and spreadsheets are not supported yet. Export to PDF first.")
        raise UnsupportedFile("This file type is not supported. Use PDF, DOCX, TXT, PNG or JPG.")
    enc = detect_text_encoding(path)
    if enc:
        return SniffResult("txt", enc)
    raise UnsupportedFile("This file type is not supported. Use PDF, DOCX, TXT, PNG or JPG.")


def detect_text_encoding(path: Path) -> str | None:
    data = path.read_bytes()[:262144]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates = ["utf-16"]
    elif b"\x00" in data:
        return None
    else:
        candidates = ["utf-8-sig", "cp1252"]
    for enc in candidates:
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            # A truncated multi-byte sequence at the sample boundary is not a failure.
            if enc.startswith("utf-8"):
                try:
                    text = data[:-4].decode(enc)
                except UnicodeDecodeError:
                    continue
            else:
                continue
        if not text.strip():
            return None
        control = sum(1 for c in text if unicodedata.category(c) == "Cc" and c not in "\r\n\t\f")
        if control <= 0.01 * len(text):
            return enc
    return None


_SAFE = re.compile(r"[^\w.() \-]+")


def sanitize_filename(name: str, fallback: str = "document") -> str:
    """Display-safe filename. Never used to build filesystem paths."""
    name = unicodedata.normalize("NFKC", name or "").replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).strip(" ._")
    if not name:
        name = fallback
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 110] + dot + ext[:8]) if dot else name[:120]
    return name
