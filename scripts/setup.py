#!/usr/bin/env python3
"""Project dependency setup (called by ./install.sh; can also be run on its own).

    python3 scripts/setup.py

Creates the backend virtual environment, installs Python and Node packages, creates .env
from .env.example, and reports which optional tools were found. Safe to run again.
System packages (Tesseract, LibreOffice) and Node.js itself are handled by ./install.sh.
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
LOCAL_NODE_BIN = ROOT / ".tools" / "node" / "bin"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"  > {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required.")
    if LOCAL_NODE_BIN.exists():
        os.environ["PATH"] = f"{LOCAL_NODE_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    npm = shutil.which("npm")
    if not npm:
        sys.exit("Node.js 20.9 or newer is required. Run ./install.sh, which can install a local copy.")

    print("Creating the Python environment (backend/.venv)")
    venv = BACKEND / ".venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)], ROOT)
    py = venv / "bin" / "python"

    print("Installing Python packages")
    run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], BACKEND)
    run([str(py), "-m", "pip", "install", "--quiet", "-r", "requirements-dev.txt"], BACKEND)

    print("Installing website packages")
    run([npm, "ci" if (FRONTEND / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"], FRONTEND)

    env = ROOT / ".env"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.example", env)
        env.chmod(0o600)  # it will hold the API key
        print("Created .env from .env.example. Open it and set ANTHROPIC_API_KEY.")
    else:
        print(".env already exists (left unchanged).")

    sys.path.insert(0, str(BACKEND))
    from annotator.engine.convert import find_soffice  # noqa: E402
    from annotator.engine.ocr import find_tesseract  # noqa: E402

    tess = find_tesseract(os.environ.get("ANNOTATOR_TESSERACT_CMD"))
    soffice = find_soffice(os.environ.get("ANNOTATOR_SOFFICE_CMD"))
    print("\nOptional tools:")
    print(f"  Tesseract OCR (scanned PDFs, images):    {tess or 'NOT FOUND'}")
    print(f"  LibreOffice (Word files, original layout): {soffice or 'NOT FOUND'}")
    if not tess or not soffice:
        print("  The app works without them, but scanned documents and images need Tesseract, and Word files")
        print("  use a simplified layout without LibreOffice. ./install.sh installs both.")


if __name__ == "__main__":
    main()
