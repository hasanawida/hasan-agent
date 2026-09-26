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
import glob
import html
import ipaddress
import json
import os
import re
import socket
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from .execution import NO_WINDOW
from .policy import APPROVAL, AUTO, FORBIDDEN, Policy, PolicyError

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
    ToolSpec("web_fetch", "pc.web", "Read a public web page as text.", {"url": "https:// URL"}),
    ToolSpec("web_search", "pc.websearch", "Search the web; returns titles, links and snippets.", {"query": "text"}),
    ToolSpec("screenshot", "pc.screenshot", "Take a screenshot of the PC screen (saved and shown to Hassan).", {}),
    ToolSpec("camera_photo", "pc.camera", "Take a photo with the PC webcam (needs ffmpeg).", {"device": "optional camera name"}),
    ToolSpec("mic_record", "pc.mic", "Record the PC microphone (needs ffmpeg).", {"seconds": "1-60", "device": "optional"}),
    ToolSpec("blender", "pc.blender", "Run a Blender Python (bpy) script in background Blender. Use it to build, "
             "edit, export or render scenes; print() what you need back. To render, set "
             "bpy.context.scene.render.filepath to the given OUTPUT path.",
             {"script": "python code using bpy", "blend_file": "optional .blend to open first",
              "save": "true to save the .blend after the script", "render": "true to render a still image"}),
    ToolSpec("ffmpeg", "pc.ffmpeg", "Edit/convert video or audio with ffmpeg (cut, join, resize, subtitles, "
             "extract audio…). Give the arguments after `ffmpeg`.", {"args": "list of arguments"}),
    ToolSpec("mcp", "pc.mcp", "Call a tool on a connected MCP server (e.g. `windows` to click/type in any app "
             "like CapCut, `blender`, `visual-studio`).",
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
                 mcp=None, command_timeout: float = 180.0, media_dir: Path | None = None, http_transport=None):
        self.policy = policy
        self.allowed_roots = allowed_roots
        self.trash_dir = trash_dir
        self.protected_dirs = [p.resolve() for p in protected_dirs]
        self.mcp = mcp
        self.command_timeout = command_timeout
        self.media_dir = media_dir or trash_dir.parent / "media"
        self.http_transport = http_transport
        self.last_media: str | None = None

    # ------------------------------------------------------------ policy
    def access(self, tool: str, args: dict) -> str:
        spec = TOOL_BY_NAME.get(tool)
        if spec is None:
            raise PolicyError(f"Unknown tool: {tool}")
        if tool == "mcp":
            server, mtool = str(args.get("server", "")), str(args.get("tool", ""))
            decision = self.policy.decide_mcp(server, mtool)
            margs = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
            # Bringing an already-open window to the front (or resizing it) changes nothing.
            if server == "windows" and mtool == "App" and decision == APPROVAL \
                    and str(margs.get("mode", "launch")) in ("switch", "resize"):
                return AUTO
            return decision
        decision = self.policy.decide(spec.action)
        if tool == "web_fetch" and decision == AUTO and len(urlsplit(str(args.get("url", ""))).query) > 300:
            return self.policy.decide("pc.web_query")  # a long query string could smuggle data out
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
            "mcp": self._describe_mcp(a),
            "camera_photo": "صوّر بكاميرا الكمبيوتر",
            "mic_record": f"سجّل من المايكروفون {a.get('seconds', 10)} ثانية",
            "blender": f"شغّل سكربت Blender{(' على ' + str(a.get('blend_file'))) if a.get('blend_file') else ''}",
            "ffmpeg": f"ffmpeg {' '.join(map(str, a.get('args') or []))[:200]}",
            "web_fetch": f"افتح رابط: {a.get('url')}",
        }.get(tool, f"{tool} {json.dumps(a, ensure_ascii=False)[:200]}")

    @staticmethod
    def _describe_mcp(a: dict) -> str:
        server, tool = a.get("server"), a.get("tool")
        m = a.get("arguments") if isinstance(a.get("arguments"), dict) else {}
        where = f"العنصر رقم {m['label']}" if m.get("label") is not None else f"المكان {m.get('loc')}" if m.get("loc") else ""
        if server == "windows":
            if tool == "App":
                mode = m.get("mode", "launch")
                what = m.get("name") or m.get("executable") or ""
                return {"launch": f"افتح برنامج: {what}", "launch_executable": f"شغّل ملف برنامج: {what}",
                        "switch": f"انتقل لبرنامج: {what}", "resize": f"غيّر حجم نافذة: {what}"}.get(mode, f"App {mode} {what}")
            if tool == "Click":
                clicks = m.get("clicks", 1)
                return f"انقر{' مرتين' if str(clicks) == '2' else ''}{' (يمين)' if m.get('button') == 'right' else ''} على {where}".strip()
            if tool == "Type":
                return f"اكتب «{str(m.get('text', ''))[:120]}» في {where}{' ثم Enter' if m.get('press_enter') else ''}"
            if tool == "Shortcut":
                return f"اختصار كيبورد: {m.get('shortcut') or m.get('keys') or m}"
            if tool == "Scroll":
                return f"مرّر {m.get('direction', 'down')}"
            if tool == "Clipboard":
                return f"الحافظة (Clipboard): {m.get('mode', '')}"
            if tool == "Process":
                return f"إدارة البرامج الشغّالة: {m.get('mode', '')} {m.get('name', '')}"
        return f"MCP {server}.{tool} {json.dumps(m, ensure_ascii=False)[:160]}"

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
            if tool == "blender":
                return str(args.get("script", ""))[:20_000]
            if tool == "ffmpeg":
                return "ffmpeg " + " ".join(map(str, args.get("args") or []))
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

    # ---- internet -------------------------------------------------------
    @staticmethod
    def _public_host(host: str) -> None:
        """Refuse local/private addresses: otherwise a web page could make us call
        Hassan's own API or the router (SSRF)."""
        if not host:
            raise PolicyError("URL without host")
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise PolicyError(f"Cannot resolve {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                    or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and ip.ipv4_mapped.is_private):
                raise PolicyError(f"{host} is a local/private address")

    async def _get(self, url: str, method: str = "GET", data: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(timeout=25, transport=self.http_transport,
                                     headers={"User-Agent": "Mozilla/5.0 HassanAI/0.3"}) as client:
            for _ in range(5):
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https"):
                    raise PolicyError("Only http(s) URLs")
                self._public_host(parts.hostname or "")
                resp = await client.request(method, url, data=data, follow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = str(httpx.URL(url).join(resp.headers["location"]))
                    method, data = "GET", None
                    continue
                return resp
        raise RuntimeError("Too many redirects")

    @staticmethod
    def _html_to_text(markup: str) -> str:
        markup = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", markup)
        markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", markup)
        text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()

    async def _t_web_fetch(self, url: str) -> str:
        resp = await self._get(url)
        ctype = resp.headers.get("content-type", "")
        body = resp.text if ("text" in ctype or "json" in ctype or "xml" in ctype) else f"({ctype}, {len(resp.content)} bytes)"
        text = self._html_to_text(body) if "html" in ctype else body
        return f"HTTP {resp.status_code} {resp.request.url}\n\n{text[:20_000]}"

    async def _t_web_search(self, query: str) -> list[dict]:
        resp = await self._get("https://html.duckduckgo.com/html/", "POST", {"q": query})
        results = []
        for m in re.finditer(r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                             resp.text):
            href, title, rest = m.group(1), m.group(2), m.group(3)
            if "uddg=" in href:
                href = unquote(parse_qs(urlsplit(html.unescape(href)).query).get("uddg", [href])[0])
            snip = re.search(r'(?s)class="result__snippet"[^>]*>(.*?)</a>', rest)
            results.append({"title": self._html_to_text(title), "url": href,
                            "snippet": self._html_to_text(snip.group(1)) if snip else ""})
            if len(results) >= 8:
                break
        return results or [{"note": "no results parsed (search page format may have changed); try web_fetch"}]

    # ---- screen / camera / mic -------------------------------------------
    def _media_path(self, prefix: str, ext: str) -> Path:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        return self.media_dir / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid() % 1000}{ext}"

    def _saved(self, path: Path, what: str) -> dict:
        self.last_media = path.name
        return {"saved": str(path), "media": path.name, "note": f"{what} saved; Hassan can see it in the dashboard"}

    async def _proc(self, argv: list[str], timeout: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    stdin=asyncio.subprocess.DEVNULL, **NO_WINDOW)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            out, _ = await proc.communicate()
            return -1, out.decode("utf-8", "replace") + "\n(timed out)"
        return proc.returncode or 0, out.decode("utf-8", "replace")

    async def _t_screenshot(self) -> dict:
        path = self._media_path("screen", ".png")
        if os.name == "nt":
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                  "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                  "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
                  f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)")
            code, out = await self._proc(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], 30)
        else:
            tool = shutil.which("gnome-screenshot") or shutil.which("scrot") or shutil.which("import")
            if not tool:
                raise RuntimeError("No screenshot tool found on this system")
            argv = {"gnome-screenshot": [tool, "-f", str(path)], "scrot": [tool, str(path)],
                    "import": [tool, "-window", "root", str(path)]}[Path(tool).name]
            code, out = await self._proc(argv, 30)
        if code != 0 or not path.exists():
            raise RuntimeError(f"screenshot failed: {out[-400:]}")
        return self._saved(path, "Screenshot")

    @staticmethod
    def _ffmpeg() -> str:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg is not installed. On Windows run:  winget install Gyan.FFmpeg  (then restart Hassan)")
        return exe

    async def _dshow_device(self, kind: str) -> str:
        code, out = await self._proc([self._ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], 20)
        m = re.search(rf'"([^"]+)"\s*\({kind}\)', out)
        if not m:
            raise RuntimeError(f"No {kind} device found:\n{out[-600:]}")
        return m.group(1)

    async def _t_camera_photo(self, device: str = "") -> dict:
        path = self._media_path("camera", ".jpg")
        if os.name == "nt":
            dev = device or await self._dshow_device("video")
            argv = [self._ffmpeg(), "-hide_banner", "-y", "-f", "dshow", "-i", f"video={dev}", "-frames:v", "1", str(path)]
        else:
            argv = [self._ffmpeg(), "-hide_banner", "-y", "-f", "v4l2", "-i", device or "/dev/video0", "-frames:v", "1", str(path)]
        code, out = await self._proc(argv, 30)
        if code != 0 or not path.exists():
            raise RuntimeError(f"camera failed: {out[-500:]}")
        return self._saved(path, "Photo")

    async def _t_mic_record(self, seconds: Any = 10, device: str = "") -> dict:
        secs = max(1, min(60, int(float(seconds or 10))))
        path = self._media_path("mic", ".m4a")
        if os.name == "nt":
            dev = device or await self._dshow_device("audio")
            src = ["-f", "dshow", "-i", f"audio={dev}"]
        else:
            src = ["-f", "pulse", "-i", device or "default"]
        code, out = await self._proc([self._ffmpeg(), "-hide_banner", "-y", *src, "-t", str(secs), str(path)], secs + 30)
        if code != 0 or not path.exists():
            raise RuntimeError(f"recording failed: {out[-500:]}")
        return self._saved(path, f"{secs}s recording")

    # ---- Blender / ffmpeg ---------------------------------------------------
    @staticmethod
    def _blender() -> str:
        exe = shutil.which("blender")
        if exe:
            return exe
        for pattern in (r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
                        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender*\blender.exe"),
                        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"):
            found = sorted(glob.glob(pattern))
            if found:
                return found[-1]
        raise RuntimeError("Blender not found. Install it from blender.org (or add blender to PATH).")

    async def _t_blender(self, script: str, blend_file: str = "", save: Any = False, render: Any = False) -> dict:
        exe = self._blender()
        out_png = self._media_path("render", ".png")
        header = (f"import bpy\nOUTPUT = {str(out_png)!r}\n"
                  "bpy.context.scene.render.filepath = OUTPUT\n")
        footer = ""
        if str(save).lower() in ("true", "1", "yes"):
            footer += "\nbpy.ops.wm.save_mainfile()\n" if blend_file else ""
        if str(render).lower() in ("true", "1", "yes"):
            footer += "\nbpy.context.scene.render.filepath = OUTPUT\nbpy.ops.render.render(write_still=True)\n"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        script_path = self.media_dir / f"script-{time.strftime('%Y%m%d-%H%M%S')}.py"
        script_path.write_text(header + str(script) + footer, encoding="utf-8")
        argv = [exe, "-b"]
        if blend_file:
            argv.append(str(self.path(blend_file, must_exist=True)))
        argv += ["--python-exit-code", "1", "--python", str(script_path)]
        code, out = await self._proc(argv, max(self.command_timeout, 900))
        result: dict = {"exit": code, "output": out[-6000:]}
        if out_png.exists():
            result.update(self._saved(out_png, "Render"))
        return result

    async def _t_ffmpeg(self, args: Any) -> str:
        argv = args if isinstance(args, list) else str(args).split()
        code, out = await self._proc([self._ffmpeg(), "-hide_banner", "-y", *map(str, argv)], max(self.command_timeout, 1800))
        return f"exit {code}\n{out[-4000:]}"

    async def _t_mcp(self, server: str, tool: str, arguments: dict | None = None) -> Any:
        if self.mcp is None:
            raise RuntimeError("No MCP servers configured")
        # access() already routed mutating MCP tools through a human approval
        return await self.mcp.call_tool(server, tool, arguments or {}, approved=True)
