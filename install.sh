#!/usr/bin/env bash
# One-command setup for AI Annotator on Linux.
#
#   ./install.sh            install everything (asks for sudo for system packages)
#   ./install.sh --yes      same, without prompts
#   ./install.sh --no-system           skip system packages (OCR, LibreOffice, python venv)
#   ./install.sh --no-libreoffice      skip LibreOffice (large; only needed for best .docx layout)
#
# What it does:
#   1. Installs system packages with your package manager (apt, dnf, pacman or zypper):
#      Python venv support, Tesseract OCR (English), LibreOffice Writer.
#   2. Uses your Node.js if it is 20.9 or newer; otherwise downloads the official Node.js 22 LTS
#      into .tools/node (checksum verified, no sudo, nothing installed system-wide).
#   3. Creates backend/.venv, installs Python and frontend packages, and creates .env.
# It is safe to run again. No GPU is required.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_VERSION="22.22.0"
ASSUME_YES=0
WITH_SYSTEM=1
WITH_LIBREOFFICE=1
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    --no-system) WITH_SYSTEM=0 ;;
    --no-libreoffice) WITH_LIBREOFFICE=0 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

if [ "$(uname -s)" != "Linux" ]; then
  die "This installer targets Linux. On other systems run: python3 scripts/setup.py"
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else SUDO=""; fi
fi

# ---------------------------------------------------------------------------------------------
say "1/3 System packages"
if [ "$WITH_SYSTEM" -eq 1 ]; then
  if [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then
    warn "sudo is not available; skipping system packages. Install Tesseract and LibreOffice manually if needed."
  else
    if command -v apt-get >/dev/null 2>&1; then
      PKGS=(python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-eng curl ca-certificates xz-utils)
      [ "$WITH_LIBREOFFICE" -eq 1 ] && PKGS+=(libreoffice-writer-nogui)
      $SUDO apt-get update -q
      if ! $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${PKGS[@]}"; then
        # Older releases do not have the -nogui variant.
        PKGS=("${PKGS[@]/libreoffice-writer-nogui/libreoffice-writer}")
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${PKGS[@]}"
      fi
    elif command -v dnf >/dev/null 2>&1; then
      PKGS=(python3 python3-pip tesseract tesseract-langpack-eng curl xz)
      [ "$WITH_LIBREOFFICE" -eq 1 ] && PKGS+=(libreoffice-writer)
      $SUDO dnf install -y "${PKGS[@]}"
    elif command -v pacman >/dev/null 2>&1; then
      PKGS=(python python-pip tesseract tesseract-data-eng curl xz)
      [ "$WITH_LIBREOFFICE" -eq 1 ] && PKGS+=(libreoffice-fresh)
      $SUDO pacman -S --needed --noconfirm "${PKGS[@]}"
    elif command -v zypper >/dev/null 2>&1; then
      PKGS=(python3 python3-pip tesseract-ocr tesseract-ocr-traineddata-english curl xz)
      [ "$WITH_LIBREOFFICE" -eq 1 ] && PKGS+=(libreoffice-writer)
      $SUDO zypper --non-interactive install "${PKGS[@]}"
    else
      warn "Unknown package manager. Install Python 3.11+, Tesseract and LibreOffice yourself."
    fi
  fi
else
  echo "Skipped (--no-system)."
fi

# ---------------------------------------------------------------------------------------------
say "2/3 Python and Node.js"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  # Needs 3.11+ and working venv support (Debian/Ubuntu ship venv separately).
  if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.11+ with venv support is required. On Debian/Ubuntu: sudo apt install python3-venv
(on Ubuntu 22.04: sudo apt install python3.11 python3.11-venv)"
echo "Python: $PYTHON ($("$PYTHON" --version))"

node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>20||(a===20&&b>=9)?0:1)'
}

LOCAL_NODE="$ROOT/.tools/node"
if [ -x "$LOCAL_NODE/bin/node" ]; then
  export PATH="$LOCAL_NODE/bin:$PATH"
fi
if node_ok; then
  echo "Node.js: $(command -v node) ($(node --version))"
else
  case "$(uname -m)" in
    x86_64) NODE_ARCH="x64" ;;
    aarch64|arm64) NODE_ARCH="arm64" ;;
    *) die "No Node.js 20.9+ found and no prebuilt download for $(uname -m). Install Node.js 22 manually." ;;
  esac
  NAME="node-v${NODE_VERSION}-linux-${NODE_ARCH}"
  URL="https://nodejs.org/dist/v${NODE_VERSION}"
  echo "Node.js 20.9+ not found. Downloading official Node.js ${NODE_VERSION} into .tools/node"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "$URL/$NAME.tar.xz" -o "$TMP/$NAME.tar.xz"
  curl -fsSL "$URL/SHASUMS256.txt" -o "$TMP/SHASUMS256.txt"
  (cd "$TMP" && grep " $NAME.tar.xz\$" SHASUMS256.txt | sha256sum -c -) || die "Node.js download failed its checksum check."
  rm -rf "$LOCAL_NODE"
  mkdir -p "$ROOT/.tools"
  tar -xJf "$TMP/$NAME.tar.xz" -C "$ROOT/.tools"
  mv "$ROOT/.tools/$NAME" "$LOCAL_NODE"
  export PATH="$LOCAL_NODE/bin:$PATH"
  echo "Node.js: $(command -v node) ($(node --version))"
fi

# ---------------------------------------------------------------------------------------------
say "3/3 Project dependencies"
"$PYTHON" "$ROOT/scripts/setup.py"

say "Done. Start the app with:  ./start.sh"
