"""PC tools for Operator mode: files, apps and commands on Hassan's computer.

Every tool has an access class decided by the policy file:

* auto      – read-only / harmless (list, read, search, info, open a document or URL, mkdir)
* approval  – changes things (write, move, copy, delete, run a command, launch a program)
* forbidden – never

Safety rails that no model output can bypass:
* every path must resolve inside HASSAN_ALLOWED_ROOTS (symlinks resolved)
* secret/credential files and Hassan's own data (access key, API keys) are never touched
* `delete` moves to Hassan's trash folder (recoverable), it never erases
* commands only run after a human approved the exact command text
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .execution import NO_WINDOW
from .policy import AUTO, FORBIDDEN, Policy, PolicyError

MAX_READ = 60_000
MAX_OUTPUT = 12_000
EXECUTABLE_SUFFIXES = {".exe", ".bat", ".cmd", ".ps1", ".msi", ".vbs", ".js", ".jse", ".wsf", ".scr",
                       ".com", ".lnk", ".reg", ".hta", ".cpl", ".jar", ".sh", ".app", ".appref-ms"}
# Folders an agent must never read or write, wherever they live.
PROTECTED_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "credentials", "Credentials",
                   "Microsoft\\Protect", "Microsoft\\Credentials"}
PROTECTED_NAMES = {"Login Data", "Cookies", "Web Data", "Local State", "key4.db", "logins.json",
                   "access_key", "hassan.env", "NTUSER.DAT", "wallet.dat"}


@dataclass
class ToolSpec:
    name: str
    action: str  # policy action name
    description: str
    args: dict[str, str]


TOOLS: list[ToolSpec] = [
    ToolSpec("list_dir", "pc.list", "List a folder (names, sizes, dates).", {"path": "folder path"}),
    ToolSpec("read_file", "pc.read", "Read a text file.", {"path": "file path"}),
    ToolSpec("search_files", "pc.search", "Find files by name pattern (and optional text) under a folder.",
             {"root": "folder", "pattern": "glob like *.pdf", "contains": "optional text to find inside"}),
    ToolSpec("system_info", "pc.info", "Computer info: OS, user folders, free disk space.", {}),
    ToolSpec("make_dir", "pc.mkdir", "Create a folder (and parents).", {"path": "folder path"}),
    ToolSpec("open", "pc.open", "Open a folder, document or http(s) URL with its default app. "
             "Programs/scripts need approval.", {"target": "path or URL"}),
    ToolSpec("write_file", "pc.write", "Create or overwrite a text file.", {"path": "file path", "content": "full text"}),
    ToolSpec("move", "pc.move", "Move or rename a file/folder.", {"src": "path", "dst": "new path"}),
    ToolSpec("copy", "pc.copy", "Copy a file/folder.", {"src": "path", "dst": "destination path"}),
    ToolSpec("delete", "pc.delete", "Delete a file/folder (goes to Hassan's recoverable trash).", {"path": "path"}),
    ToolSpec("run", "pc.run", "Run a command (PowerShell on Windows). Always shown to Hassan first.",
             {"command": "the exact command", "cwd": "optional working folder"}),
    ToolSpec("mcp", "pc.mcp", "Call a tool on a connected MCP server (Blender, Visual Studio…).",
             {"server": "server name", "tool": "tool name", "arguments": "object"}),
]
TOOL_BY_NAME = {t.name: t for t in TOOLS}


def tools_prompt() -> str:
    lines = []
    for t in TOOLS:
        args = ", ".join(f'"{k}": {v}' for k, v in t.args.items())
        lines.append(f"- {t.name}({args}) — {t.description}")
    return "\n".join(lines)


class PCTools:
    def __init__(self, policy: Policy, allowed_roots: list[Path], trash_dir: Path, protected_dirs: list[Path],
                 mcp=None, command_timeout: float = 180.0):
        self.policy = policy
        self.allowed_roots = allowed_roots
        self.trash_dir = trash_dir
        self.protected_dirs = [p.resolve() for p in protected_dirs]
        self.mcp = mcp
        self.command_timeout = command_timeout

    # ------------------------------------------------------------ policy
    def access(self, tool: str, args: dict) -> str:
        spec = TOOL_BY_NAME.get(tool)
        if spec is None:
            raise PolicyError(f"Unknown tool: {tool}")
        if tool == "mcp":
            return self.policy.decide_mcp(str(args.get("server", "")), str(args.get("tool", "")))
        decision = self.policy.decide(spec.action)
        if tool == "open" and decision == AUTO:
            target = str(args.get("target", ""))
            if not target.lower().startswith(("http://", "https://")):
                path = self.path(target)
                if path.suffix.lower() in EXECUTABLE_SUFFIXES:
                    return self.policy.decide("pc.launch")
        return decision

    def path(self, raw: str, must_exist: bool = False) -> Path:
        if not raw or not str(raw).strip():
            raise PolicyError("Empty path")
        p = Path(os.path.expandvars(str(raw).strip().strip('"'))).expanduser()
        if not p.is_absolute():
            p = self.allowed_roots[0] / p
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self.allowed_roots):
            roots = ", ".join(str(r) for r in self.allowed_roots)
            raise PolicyError(f"{p} is outside the allowed folders ({roots})")
        if any(p == d or d in p.parents for d in self.protected_dirs):
            raise PolicyError(f"{p} is protected (Hassan's private data)")
        parts = set(p.parts)
        joined = str(p)
        if parts & PROTECTED_PARTS or any(f"\\{x}\\" in joined for x in PROTECTED_PARTS if "\\" in x):
            raise PolicyError(f"{p} is a protected credentials folder")
        if p.name in PROTECTED_NAMES or self.policy.is_secret(p.name):
            raise PolicyError(f"{p.name} is a protected secret file")
        if must_exist and not p.exists():
            raise FileNotFoundError(str(p))
        return p

    def describe(self, tool: str, args: dict) -> str:
        """Human-readable one-liner for the approval card."""
        a = args
        return {
            "write_file": f"اكتب ملف: {a.get('path')}",
            "move": f"انقل: {a.get('src')} ← إلى → {a.get('dst')}",
            "copy": f"انسخ: {a.get('src')} ← إلى → {a.get('dst')}",
            "delete": f"احذف (لسلة Hassan): {a.get('path')}",
            "run": f"شغّل أمر: {a.get('command')}",
            "open": f"شغّل برنامج: {a.get('target')}",
            "mcp": f"MCP {a.get('server')}.{a.get('tool')}",
        }.get(tool, f"{tool} {json.dumps(a, ensure_ascii=False)[:200]}")

    def preview(self, tool: str, args: dict) -> str:
        """Extra detail for the approval card (diff, command, sizes)."""
        try:
            if tool == "write_file":
                import difflib
                p = self.path(args.get("path", ""))
                before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
                return "".join(difflib.unified_diff(before.splitlines(True), str(args.get("content", "")).splitlines(True),
                                                    fromfile=str(p), tofile=str(p)))[:100_000]
            if tool == "run":
                return f"$ {args.get('command')}\n(cwd: {args.get('cwd') or self.allowed_roots[0]})"
            if tool in ("move", "copy", "delete"):
                p = self.path(args.get("src") or args.get("path") or "")
                if p.is_dir():
                    n = sum(1 for _ in p.rglob("*"))
                    return f"{p} — folder, {n} item(s)"
                return f"{p} — {p.stat().st_size if p.exists() else 0} bytes"
        except Exception as exc:  # noqa: BLE001 - preview is best-effort
            return f"(preview unavailable: {exc})"
        return json.dumps(args, ensure_ascii=False, indent=2)[:4000]

    # ------------------------------------------------------------ execution
    async def execute(self, tool: str, args: dict) -> str:
        fn = getattr(self, f"_t_{tool}", None)
        if fn is None:
            raise PolicyError(f"Unknown tool: {tool}")
        if self.access(tool, args) == FORBIDDEN:
            raise PolicyError(f"{tool} is forbidden by policy")
        result = fn(**{k: v for k, v in args.items() if k in TOOL_BY_NAME[tool].args})
        if asyncio.iscoroutine(result):
            result = await result
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1, default=str)
        return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n…(truncated)"

    def _t_list_dir(self, path: str) -> list[dict]:
        p = self.path(path, must_exist=True)
        items = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:500]:
            try:
                st = child.stat()
                items.append({"name": child.name + ("/" if child.is_dir() else ""),
                              "size": st.st_size if child.is_file() else None,
                              "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
            except OSError:
                items.append({"name": child.name, "error": "unreadable"})
        return items

    def _t_read_file(self, path: str) -> str:
        p = self.path(path, must_exist=True)
        if p.is_dir():
            raise IsADirectoryError(str(p))
        return p.read_text(encoding="utf-8", errors="replace")[:MAX_READ]

    def _t_search_files(self, root: str, pattern: str = "*", contains: str = "") -> list[str]:
        base = self.path(root, must_exist=True)
        found: list[str] = []
        needle = (contains or "").lower()
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "$RECYCLE.BIN")]
            for name in filenames:
                if not fnmatch.fnmatch(name.lower(), (pattern or "*").lower()):
                    continue
                full = Path(dirpath) / name
                if needle:
                    try:
                        if full.stat().st_size > 5_000_000 or needle not in full.read_text(
                                encoding="utf-8", errors="ignore").lower():
                            continue
                    except OSError:
                        continue
                found.append(str(full))
                if len(found) >= 200:
                    return found
        return found

    def _t_system_info(self) -> dict:
        home = Path.home()
        info: dict[str, Any] = {"os": platform.platform(), "user": os.environ.get("USERNAME") or os.environ.get("USER"),
                                "home": str(home), "allowed_roots": [str(r) for r in self.allowed_roots],
                                "folders": {n: str(home / n) for n in ("Desktop", "Documents", "Downloads", "Pictures",
                                                                       "Videos", "Music") if (home / n).exists()}}
        disks = {}
        for r in self.allowed_roots:
            try:
                u = shutil.disk_usage(r)
                disks[str(r.anchor or r)] = {"free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)}
            except OSError:
                pass
        info["disks"] = disks
        return info

    def _t_make_dir(self, path: str) -> str:
        p = self.path(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"created {p}"

    def _t_open(self, target: str) -> str:
        if target.lower().startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"opened {target}"
        p = self.path(target, must_exist=True)
        if os.name == "nt":
            os.startfile(str(p))  # noqa: S606 - default app, approved when executable
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(p)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"opened {p}"

    def _t_write_file(self, path: str, content: str) -> str:
        p = self.path(path)
        if p.exists():
            self._to_trash(p, copy=True)  # keep the old version recoverable
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        return f"wrote {p} ({len(str(content))} chars)"

    def _t_move(self, src: str, dst: str) -> str:
        s, d = self.path(src, must_exist=True), self.path(dst)
        if d.exists() and d.is_dir():
            d = self.path(str(d / s.name))
        if d.exists():
            raise FileExistsError(f"{d} already exists")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"moved {s} -> {d}"

    def _t_copy(self, src: str, dst: str) -> str:
        s, d = self.path(src, must_exist=True), self.path(dst)
        if d.exists() and d.is_dir():
            d = self.path(str(d / s.name))
        if d.exists():
            raise FileExistsError(f"{d} already exists")
        d.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if s.is_dir() else shutil.copy2)(str(s), str(d))
        return f"copied {s} -> {d}"

    def _t_delete(self, path: str) -> str:
        p = self.path(path, must_exist=True)
        if p in self.allowed_roots:
            raise PolicyError("Refusing to delete a whole allowed root folder")
        where = self._to_trash(p)
        return f"moved to Hassan trash: {where}"

    def _to_trash(self, p: Path, copy: bool = False) -> Path:
        dest = self.trash_dir / time.strftime("%Y%m%d-%H%M%S") / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            (shutil.copytree if p.is_dir() else shutil.copy2)(str(p), str(dest))
        else:
            shutil.move(str(p), str(dest))
        (dest.parent / "ORIGINAL_PATH.txt").write_text(str(p), encoding="utf-8")
        return dest

    async def _t_run(self, command: str, cwd: str = "") -> str:
        workdir = self.path(cwd) if cwd else self.allowed_roots[0]
        if os.name == "nt":
            argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
        else:
            argv = ["/bin/sh", "-c", command]
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(workdir), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
                                                    **NO_WINDOW)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.command_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            return f"timed out after {self.command_timeout:.0f}s\n{out.decode('utf-8', 'replace')[-4000:]}"
        text = out.decode("utf-8", "replace") + (("\n[stderr]\n" + err.decode("utf-8", "replace")) if err else "")
        return f"exit {proc.returncode}\n{text.strip()}"

    async def _t_mcp(self, server: str, tool: str, arguments: dict | None = None) -> Any:
        if self.mcp is None:
            raise RuntimeError("No MCP servers configured")
        # access() already routed mutating MCP tools through a human approval
        return await self.mcp.call_tool(server, tool, arguments or {}, approved=True)
