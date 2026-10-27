#!/usr/bin/env python3
"""One-time setup for local development (Windows, macOS, Linux).

    python scripts/setup.py        (Windows: py scripts\\setup.py)

Creates the backend virtual environment, installs Python and Node dependencies,
creates .env from .env.example, and reports which optional tools are available.
Safe to run again at any time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"  > {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required. Download it from https://www.python.org/downloads/")
    npm = shutil.which("npm.cmd" if IS_WINDOWS else "npm") or shutil.which("npm")
    if not npm:
        sys.exit("Node.js 20 or newer is required. Download it from https://nodejs.org/")

    print("1/4 Creating the Python environment (backend/.venv)")
    venv = BACKEND / ".venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)], ROOT)
    py = venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")

    print("2/4 Installing Python packages")
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"], BACKEND)
    run([str(py), "-m", "pip", "install", "-r", "requirements-dev.txt"], BACKEND)

    print("3/4 Installing frontend packages")
    run([npm, "install"], FRONTEND)

    print("4/4 Configuration")
    env = ROOT / ".env"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.example", env)
        print("  Created .env from .env.example. Open it and set ANTHROPIC_API_KEY.")
    else:
        print("  .env already exists (left unchanged).")

    sys.path.insert(0, str(BACKEND))
    from annotator.engine.convert import find_soffice  # noqa: E402
    from annotator.engine.ocr import find_tesseract  # noqa: E402

    print("\nOptional tools:")
    tess = find_tesseract(os.environ.get("ANNOTATOR_TESSERACT_CMD"))
    print(f"  Tesseract OCR (scanned PDFs, images): {'found at ' + tess if tess else 'NOT FOUND'}")
    soffice = find_soffice(os.environ.get("ANNOTATOR_SOFFICE_CMD"))
    print(f"  LibreOffice (Word documents, best layout): {'found at ' + soffice if soffice else 'NOT FOUND'}")
    if not tess or not soffice:
        print("  See README.md > Optional tools for install instructions. The app works without them,")
        print("  but scanned documents and images need Tesseract, and Word files use a simpler layout without LibreOffice.")
    print("\nSetup complete. Start the app with:  python dev.py" + ("   (or: py dev.py)" if IS_WINDOWS else ""))


if __name__ == "__main__":
    main()
