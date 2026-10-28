#!/usr/bin/env python3
"""Start everything AI Annotator needs: API, background worker and website.

Normally run through ./start.sh (which uses the project's virtual environment):

    ./start.sh              development mode       http://localhost:3000
    ./start.sh --prod       optimised website build (built once, then served)
    ./start.sh --lan        reachable from other computers on your network (implies --prod)

Output is prefixed with [api], [worker] and [web]. Press Ctrl+C to stop everything; a job in
progress is handed back to the queue and resumes the next time the app starts.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
LOCAL_NODE_BIN = ROOT / ".tools" / "node" / "bin"
COLORS = {"api": "\033[36m", "worker": "\033[35m", "web": "\033[32m", "build": "\033[33m"}
RESET = "\033[0m"


def venv_python() -> Path:
    return BACKEND / ".venv" / "bin" / "python"


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    sys.exit(1)


def preflight() -> None:
    if not venv_python().exists():
        fail("The backend is not installed yet. Run:  ./install.sh")
    if not (FRONTEND / "node_modules").exists():
        fail("The frontend is not installed yet. Run:  ./install.sh")
    if not shutil.which("npm"):
        fail("Node.js (npm) was not found. Run ./install.sh, which can install a local copy.")
    if not (ROOT / ".env").exists():
        print("Note: no .env file found. Copy .env.example to .env and add your ANTHROPIC_API_KEY.\n")


def lan_addresses() -> list[str]:
    """IPv4 addresses other computers on the network can use to reach this one."""
    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outbound interface
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        found.update(a for a in out.split() if a.count(".") == 3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sorted(a for a in found if not a.startswith("127."))


def start(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, start_new_session=True)
    color = COLORS.get(name, "") if sys.stdout.isatty() else ""
    reset = RESET if color else ""

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"{color}[{name:<6}]{reset} {line}", end="", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    return proc


def stop(procs: dict[str, subprocess.Popen]) -> None:
    # Ask nicely first so the worker can hand its job back to the queue.
    for p in procs.values():
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGINT)
            except OSError:
                pass
    deadline = time.time() + 8
    for p in procs.values():
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            pass
    for p in procs.values():
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI Annotator locally.")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--prod", action="store_true", help="Build the website once and serve the optimised version.")
    parser.add_argument("--lan", action="store_true",
                        help="Listen on all network interfaces so other computers can connect (implies --prod). "
                             "There are no accounts yet: only use this on a network you trust.")
    parser.add_argument("--no-web", action="store_true", help="Start only the API and worker.")
    args = parser.parse_args()
    if args.lan:
        args.prod = True  # the Next.js dev server only accepts connections from this computer

    # Make Ctrl+C and termination requests always reach the shutdown logic, even when the
    # launching shell started us with SIGINT ignored (e.g. as a background job).
    signal.signal(signal.SIGINT, signal.default_int_handler)

    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    if LOCAL_NODE_BIN.exists():
        os.environ["PATH"] = f"{LOCAL_NODE_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    preflight()
    py = str(venv_python())
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    addresses = lan_addresses() if args.lan else []
    origins = [f"http://localhost:{args.web_port}", f"http://127.0.0.1:{args.web_port}"]
    origins += [f"http://{a}:{args.web_port}" for a in addresses]
    hostname = socket.gethostname()
    if args.lan and hostname:
        origins.append(f"http://{hostname}:{args.web_port}")
    env.setdefault("ANNOTATOR_CORS_ORIGINS", ",".join(origins))

    web_env = dict(env, PORT=str(args.web_port), NEXT_TELEMETRY_DISABLED="1")
    if args.lan:
        # The browser reaches the API on whichever address it used to open the website.
        web_env.pop("NEXT_PUBLIC_API_URL", None)
        web_env["NEXT_PUBLIC_API_PORT"] = str(args.api_port)
    else:
        web_env.setdefault("NEXT_PUBLIC_API_URL", f"http://127.0.0.1:{args.api_port}")

    if args.prod and not args.no_web:
        print("Building the website (takes about a minute the first time)...")
        build = subprocess.run(["npm", "run", "build"], cwd=FRONTEND, env=web_env)
        if build.returncode != 0:
            fail("The website build failed. See the output above.")

    procs: dict[str, subprocess.Popen] = {}
    try:
        procs["api"] = start("api", [py, "-m", "uvicorn", "annotator.api.app:get_app", "--factory", "--host", host,
                                     "--port", str(args.api_port)], BACKEND, env)
        procs["worker"] = start("worker", [py, "-m", "annotator.worker"], BACKEND, env)
        if not args.no_web:
            mode = "start" if args.prod else "dev"
            procs["web"] = start("web", ["npm", "run", mode, "--", "--hostname", host, "--port", str(args.web_port)],
                                 FRONTEND, web_env)

        print(f"\nAI Annotator is starting. Open http://localhost:{args.web_port} once [web] reports it is ready.")
        for a in addresses:
            print(f"From other computers on your network: http://{a}:{args.web_port}")
        if args.lan:
            print("Warning: there are no accounts yet. Anyone on this network who can reach these addresses can use "
                  "the app and open results by their link.")
        print("Press Ctrl+C to stop.\n")
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
