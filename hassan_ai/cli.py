"""`hassan` command line.

    hassan serve            run the server in the foreground
    hassan start            start the server in the background (no window) if not running
    hassan stop             stop the background server
    hassan open             start if needed, then open the dashboard
    hassan status           is it running? which brains are connected?
    hassan ask "..."        send a task (workspace = current folder), e.g. from VS Code's terminal
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

from .config import Settings


def _base(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.port}"


def _pid_file(settings: Settings) -> Path:
    return settings.data_dir / "server.pid"


def is_running(settings: Settings) -> bool:
    try:
        return httpx.get(f"{_base(settings)}/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def serve(settings: Settings) -> None:
    import uvicorn

    from .server import create_app

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # pythonw.exe: no console, log to a file
        log = open(settings.data_dir / "server.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = log
    if is_running(settings):
        print(f"Hassan AI OS is already running on {_base(settings)}")
        return
    _pid_file(settings).write_text(str(os.getpid()))
    try:
        # Quiet console: the dashboard polls every second, so per-request access logs are noise.
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                    access_log=False, use_colors=False, ws_max_size=4096, ws_max_queue=16)
    finally:
        _pid_file(settings).unlink(missing_ok=True)


def start(settings: Settings, wait: float = 30.0) -> bool:
    if is_running(settings):
        return True
    python = Path(sys.executable)
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "cwd": str(Path(__file__).resolve().parents[1])}
    if os.name == "nt":
        pythonw = python.with_name("pythonw.exe")
        python = pythonw if pythonw.exists() else python
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(python), "-m", "hassan_ai", "serve"], **kwargs)
    deadline = time.time() + wait
    while time.time() < deadline:
        if is_running(settings):
            return True
        time.sleep(0.5)
    return False


def stop(settings: Settings) -> bool:
    pid_file = _pid_file(settings)
    if not pid_file.exists():
        return False
    pid = int(pid_file.read_text().strip() or 0)
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(pid, 15)
    except OSError:
        pass
    # taskkill returns before the process is gone. Wait until the port is free, otherwise the
    # next `start` sees the old server still answering, thinks all is well, and then it dies.
    deadline = time.time() + 15
    while time.time() < deadline and is_running(settings):
        time.sleep(0.3)
    pid_file.unlink(missing_ok=True)
    return True


def ask(settings: Settings, prompt: str, workspace: str | None, mode: str, execute: bool,
        project: str | None, open_browser: bool) -> dict:
    if not start(settings):
        raise SystemExit("Hassan AI OS did not start. See data/server.log")
    body = {"prompt": prompt, "mode": mode, "execute": execute, "project": project,
            "workspace": str(Path(workspace).resolve()) if workspace else None}
    resp = httpx.post(f"{_base(settings)}/api/tasks", json=body, timeout=30)
    if resp.status_code >= 400:
        raise SystemExit(f"Error: {resp.json().get('detail', resp.text)}")
    task = resp.json()
    if open_browser:
        webbrowser.open(f"{_base(settings)}/?task={task['id']}")
    return task


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hassan", description="Hassan AI OS")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("open")
    sub.add_parser("status")
    p_ask = sub.add_parser("ask")
    p_ask.add_argument("prompt", nargs="+")
    p_ask.add_argument("-w", "--workspace", default=os.getcwd(), help="project folder (default: current folder)")
    p_ask.add_argument("--no-workspace", action="store_true")
    p_ask.add_argument("-m", "--mode", default="auto", choices=["fast", "auto", "consensus"])
    p_ask.add_argument("-x", "--execute", action="store_true", help="checkpoint + build + tests (+ edits after approval)")
    p_ask.add_argument("-p", "--project", default=None)
    p_ask.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd in (None, "serve"):
        serve(settings)
    elif args.cmd == "start":
        print("running" if start(settings) else "failed to start (see data/server.log)")
    elif args.cmd == "stop":
        print("stopped" if stop(settings) else "not running")
    elif args.cmd == "open":
        if not start(settings):
            raise SystemExit("Hassan AI OS did not start. See data/server.log")
        webbrowser.open(_base(settings))
    elif args.cmd == "status":
        if not is_running(settings):
            print("Hassan AI OS: stopped")
            raise SystemExit(1)
        health = httpx.get(f"{_base(settings)}/api/health", timeout=5).json()
        providers = httpx.get(f"{_base(settings)}/api/providers", timeout=60).json()
        print(f"Hassan AI OS {health['version']} · {health['mode']} · {_base(settings)}")
        print(json.dumps(providers.get("backends", {}), indent=2, ensure_ascii=False))
    elif args.cmd == "ask":
        task = ask(settings, " ".join(args.prompt), None if args.no_workspace else args.workspace,
                   args.mode, args.execute, args.project, not args.no_browser)
        print(f"Task {task['id']} sent → {_base(settings)}/?task={task['id']}")


if __name__ == "__main__":
    main()
