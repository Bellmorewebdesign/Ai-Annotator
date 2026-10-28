#!/usr/bin/env bash
# Start AI Annotator (API, background worker and website). Run ./install.sh once first.
#
#   ./start.sh              development mode, open http://localhost:3000
#   ./start.sh --prod       build the website once and serve the optimised version
#   ./start.sh --lan        also reachable from other computers on your network (no login yet!)
#   ./start.sh --help       all options
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Node.js installed by install.sh (only when the system one is missing or too old).
if [ -x "$ROOT/.tools/node/bin/node" ]; then
  export PATH="$ROOT/.tools/node/bin:$PATH"
fi

PY="$ROOT/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "AI Annotator is not installed yet. Run ./install.sh first." >&2
  exit 1
fi
exec "$PY" "$ROOT/dev.py" "$@"
