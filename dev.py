#!/usr/bin/env python3
"""Start everything AI Annotator needs for local development.

    python dev.py            (Windows: py dev.py)

Runs three processes and prefixes their output:
  [api]     FastAPI backend          http://127.0.0.1:8000
  [worker]  background job worker
  [web]     Next.js frontend         http://localhost:3000

Press Ctrl+C to stop all of them. Jobs in progress resume the next time the worker starts.
Run `python scripts/setup.py` once before the first start.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"
COLORS = {"api": "\033[36m", "worker": "\033[35m", "web": "\033[32m"}
RESET = "\033[0m"


def venv_python() -> Path:
    return BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    sys.exit(1)


def preflight() -> None:
    if not venv_python().exists():
        fail("The backend is not set up yet. Run:  python scripts/setup.py")
    if not (FRONTEND / "node_modules").exists():
        fail("The frontend is not set up yet. Run:  python scripts/setup.py")
    if not shutil.which("npm"):
        fail("Node.js (npm) was not found. Install Node.js 20 or newer from https://nodejs.org")
    if not (ROOT / ".env").exists():
        print("Note: no .env file found. Copy .env.example to .env and add your ANTHROPIC_API_KEY.\n")


def start(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
    color = COLORS.get(name, "") if sys.stdout.isatty() or IS_WINDOWS else ""
    reset = RESET if color else ""

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"{color}[{name:<6}]{reset} {line}", end="", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    return proc


def stop(procs: dict[str, subprocess.Popen]) -> None:
    # Ask nicely first so the worker can hand its job back to the queue.
    for name, p in procs.items():
        if p.poll() is not None:
            continue
        try:
            if IS_WINDOWS:
                p.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(p.pid, signal.SIGINT)
        except (OSError, ValueError):
            pass
    deadline = time.time() + 8
    for p in procs.values():
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            pass
    for p in procs.values():
        if p.poll() is None:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI Annotator locally.")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--no-web", action="store_true", help="Start only the API and worker.")
    args = parser.parse_args()

    if IS_WINDOWS:
        os.system("")  # enable ANSI colours in the Windows console
    # Make Ctrl+C and termination requests always reach the shutdown logic, even when the
    # launching shell started us with SIGINT ignored (e.g. as a background job).
    signal.signal(signal.SIGINT, signal.default_int_handler)

    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    preflight()
    py = str(venv_python())
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    web_origin = f"http://localhost:{args.web_port},http://127.0.0.1:{args.web_port}"
    env.setdefault("ANNOTATOR_CORS_ORIGINS", web_origin)

    procs: dict[str, subprocess.Popen] = {}
    procs["api"] = start("api", [py, "-m", "uvicorn", "annotator.api.app:get_app", "--factory", "--host", "127.0.0.1",
                                 "--port", str(args.api_port)], BACKEND, env)
    procs["worker"] = start("worker", [py, "-m", "annotator.worker"], BACKEND, env)
    if not args.no_web:
        web_env = dict(env, NEXT_PUBLIC_API_URL=f"http://127.0.0.1:{args.api_port}", PORT=str(args.web_port))
        npm = "npm.cmd" if IS_WINDOWS else "npm"
        procs["web"] = start("web", [npm, "run", "dev", "--", "--port", str(args.web_port)], FRONTEND, web_env)

    print(f"\nAI Annotator is starting. Open http://localhost:{args.web_port} once [web] reports it is ready.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            for name, p in procs.items():
                if p.poll() is not None:
                    print(f"\n[{name}] exited with code {p.returncode}. Stopping everything.")
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        stop(procs)


if __name__ == "__main__":
    main()
