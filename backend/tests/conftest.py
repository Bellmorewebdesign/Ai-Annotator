from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Tests must never touch the developer's real data directory or call the real API.
os.environ.setdefault("ANNOTATOR_ENV", "test")
os.environ["ANTHROPIC_API_KEY"] = ""

from annotator.engine.document import load_document  # noqa: E402
from annotator.engine.textindex import build_doc_text  # noqa: E402


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path


def load(path: Path, tesseract: str | None = None):
    res = load_document(path, tesseract=tesseract)
    return res, build_doc_text(res.doc)
