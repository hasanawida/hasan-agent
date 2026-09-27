"""FastAPI control plane + Arabic dashboard."""

from __future__ import annotations

import asyncio
import secrets
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, openhands, remote
from .agents import Roster
from .config import PACKAGE_DIR, Settings
from .execution import NO_WINDOW, CheckpointManager, ExecutionManager, SafeLocalRunner
from .desktop import DesktopControl, attach_routes
from .diagnostics import readiness
from .llm import MockLLM
from .mcp_bus import MCPRegistry
from .memory import Memory
from .operator_mode import Operator
from .orchestrator import Orchestrator
from .pc_tools import PCTools, editor_argv
from .scheduler import Scheduler
from .telegram import TelegramBot, groq_transcriber
from .policy import Policy, PolicyError, resolve_workspace
from .config import save_env_value
from .providers import APIBackend, RouterLLM
from .schemas import TaskCreate

STATIC = PACKAGE_DIR / "static"


class Decision(BaseModel):
    approve: bool
    trust_similar: bool = False


class McpCall(BaseModel):
    arguments: dict = {}


class OpenRequest(BaseModel):
    path: str
    line: int | None = None


class KeyUpdate(BaseModel):
    name: str
    key: str = ""


SIGNUP = {"groq": "https://console.groq.com/keys", "gemini": "https://aistudio.google.com/apikey",
          "openrouter": "https://openrouter.ai/keys"}


class TokenBody(BaseModel):
    token: str = ""


class ChatBody(BaseModel):
    chat_id: int


class ScheduleBody(BaseModel):
    text: str  # e.g. "daily 08:00 send me a summary"
    kind: str = "operate"


class ProfileNote(BaseModel):
    content: str


class MemoryNote(BaseModel):
    kind: str = "note"
    content: str


def create_app(settings: Settings | None = None, llm=None, telegram_transport=None,
               telegram_api: str = "https://api.telegram.org") -> FastAPI:
    settings = settings or Settings.from_env()
    memory = Memory(settings.db_path)
    policy = Policy.load(settings.policy_file)
    runner = SafeLocalRunner(settings.command_timeout)
    execution = ExecutionManager(policy, runner, CheckpointManager(settings.checkpoints_dir, runner))
    roster = Roster.load(settings.agents_config)
    if llm is None:
        llm = (RouterLLM.from_config(settings.providers_config, settings.gateway_url,
                                     settings.gateway_key, settings.request_timeout)
               if settings.mode == "live" else MockLLM())
    orchestrator = Orchestrator(settings, memory, llm, roster, execution)
    mcp = MCPRegistry(settings.mcp_config, policy)
    pc = PCTools(policy, settings.allowed_roots, settings.data_dir / "trash",
                 protected_dirs=[settings.data_dir, PACKAGE_DIR.parent], mcp=mcp, media_dir=settings.data_dir / "media",
                 command_timeout=settings.command_timeout)
    orchestrator.operator = Operator(orchestrator, pc, skills_dir=settings.data_dir / "skills")
    scheduler = Scheduler(orchestrator)
    telegram = TelegramBot(orchestrator, settings.env_file, settings.data_dir / "media", api_base=telegram_api,
                           transport=telegram_transport, scheduler=scheduler, transcriber=groq_transcriber)
    roster.agents["operator"].extra_system = orchestrator.operator.system_prompt()
    desktop = DesktopControl()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        orchestrator.recover_interrupted()
        scheduler.start()
        await telegram.start()
        desktop.start()
        yield
        await desktop.close()
        await telegram.stop()
        await stop_camera()
        await scheduler.stop()
        await orchestrator.drain()
        if hasattr(llm, "aclose"):
            await llm.aclose()

    app = FastAPI(title="Hassan AI OS", version=__version__, lifespan=lifespan)
    app.state.orchestrator = orchestrator
    app.state.memory = memory
    app.state.telegram = telegram
    app.state.scheduler = scheduler
    app.state.desktop = desktop
    attach_routes(app, settings, desktop)

    access_key = remote.load_or_create_key(settings.data_dir)
    app.state.access_key = access_key
    pairing = remote.PairingCodes()
    PUBLIC_PATHS = {"/login", "/manifest.webmanifest", "/static/icon.svg"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        local = remote.is_local(request, settings.allowed_hosts, settings.trusted_clients)
        request.state.local = local
        if not local and request.url.path not in PUBLIC_PATHS:
            # Phone / other devices: need the access key (cookie from /login or Bearer header).
            if not remote.key_ok(remote.presented_key(request), app.state.access_key):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "Access key required"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
        # Cross-site protection: a web page on another site must not drive this API.
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin_host = (urlsplit(origin).hostname or "").lower()
            if origin_host != remote.request_host(request) and origin_host not in settings.allowed_hosts:
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)

    def login_response(key: str | None, request: Request):
        if key and key.strip().isdigit() and len(key.strip()) == 6 and pairing.redeem(key):
            key = app.state.access_key
        if remote.key_ok(key, app.state.access_key):
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(remote.COOKIE, app.state.access_key, max_age=400 * 86400, httponly=True,
                            samesite="strict", secure=remote.is_https(request))
            return resp
        err = '<p class="err">المفتاح غلط</p>' if key else ""
        return HTMLResponse(remote.LOGIN_PAGE.replace("__ERR__", err), status_code=401 if key else 200)

    @app.get("/login", include_in_schema=False)
    async def login_get(request: Request, key: str | None = None):
        return login_response(key, request)

    @app.post("/login", include_in_schema=False)
    async def login_post(request: Request):
        form = (await request.body()).decode("utf-8", "replace")
        from urllib.parse import parse_qs
        key = (parse_qs(form).get("key") or [""])[0].strip()
        return login_response(key or "-", request)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        return JSONResponse({
            "name": "Hassan AI OS", "short_name": "Hassan AI", "start_url": "/", "display": "standalone",
            "dir": "rtl", "lang": "ar", "background_color": "#0b1020", "theme_color": "#0b1020",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
        }, media_type="application/manifest+json")

    def require_local(request: Request) -> None:
        if not request.state.local:
            raise HTTPException(403, "Only from the PC itself")

    @app.get("/api/remote")
    async def remote_info(request: Request):
        """Pairing info (key + QR). Only shown on the PC itself, never to remote devices."""
        require_local(request)
        port = settings.port
        urls = []
        if settings.public_url:
            urls.append({"kind": "tailscale", "base": settings.public_url})
        if settings.host in ("0.0.0.0", "::"):
            urls += [{"kind": "wifi", "base": f"http://{ip}:{port}"} for ip in remote.lan_addresses()]
        for u in urls:
            u["pair_url"] = f"{u['base']}/login?key={app.state.access_key}"
            u["qr_html"] = remote.qr_html(u["pair_url"])
        return {"enabled": bool(urls), "urls": urls, "public_url": settings.public_url, "bind": settings.host}

    @app.post("/api/remote/pair-code")
    async def remote_pair_code(request: Request):
        require_local(request)
        return pairing.new()

    @app.post("/api/remote/rotate")
    async def remote_rotate(request: Request):
        require_local(request)
        app.state.access_key = remote.rotate_key(settings.data_dir)
        await desktop.disable()
        return {"ok": True}

    def api_backends() -> dict:
        if not isinstance(llm, RouterLLM):
            return {}
        return {n: b for n, b in llm.backends.items() if isinstance(b, APIBackend)}

    @app.get("/api/keys")
    async def list_keys(request: Request):
        """Which free-API keys are saved (never returns the keys themselves). PC only."""
        require_local(request)
        return [{"name": n, "key_env": b.key_env, "set": bool(b.key), "models": b.models,
                 "signup": SIGNUP.get(n, "")} for n, b in api_backends().items()]

    @app.post("/api/keys")
    async def save_key(body: KeyUpdate, request: Request):
        require_local(request)
        backend = api_backends().get(body.name)
        if backend is None or not backend.key_env:
            raise HTTPException(404, "Unknown API backend")
        key = body.key.strip()
        if any(c.isspace() for c in key) or len(key) > 400:
            raise HTTPException(400, "That does not look like an API key")
        save_env_value(settings.env_file, backend.key_env, key)
        backend.limited_until = 0.0
        return {"name": body.name, "set": bool(key)}

    @app.post("/api/keys/{name}/test")
    async def test_key(name: str, request: Request):
        require_local(request)
        backend = api_backends().get(name)
        if backend is None:
            raise HTTPException(404, "Unknown API backend")
        try:
            comp = await backend.complete(None, "Reply with the single word: OK", "ping")
            return {"ok": True, "model": comp.model, "reply": comp.text.strip()[:80]}
        except Exception as exc:  # noqa: BLE001 - report any provider error to the user
            return {"ok": False, "error": str(exc)[:400]}

    # ---- Telegram (PC only: token and pairing) ------------------------------
    @app.get("/api/telegram")
    async def telegram_status(request: Request):
        require_local(request)
        return telegram.status()

    @app.post("/api/telegram/token")
    async def telegram_token(body: TokenBody, request: Request):
        require_local(request)
        token = body.token.strip()
        if token and (":" not in token or any(c.isspace() for c in token) or len(token) > 200):
            raise HTTPException(400, "That does not look like a bot token (123456:ABC...)")
        return await telegram.set_token(token)

    @app.post("/api/telegram/pair-code")
    async def telegram_pair_code(request: Request):
        require_local(request)
        if not telegram.token:
            raise HTTPException(400, "Save the bot token first")
        return {**telegram.pairing.new(), "bot": telegram.username}

    @app.post("/api/telegram/unpair")
    async def telegram_unpair(body: ChatBody, request: Request):
        require_local(request)
        telegram.unpair(body.chat_id)
        return telegram.status()

    # ---- schedules ---------------------------------------------------------
    @app.get("/api/schedules")
    async def list_schedules():
        return scheduler.list()

    @app.post("/api/schedules", status_code=201)
    async def add_schedule(body: ScheduleBody):
        try:
            return scheduler.add_from_text(body.text, kind=body.kind if body.kind in ("operate", "project") else "operate")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int):
        if not scheduler.remove(schedule_id):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    # ---- memory about Hassan + learned skills ------------------------------
    @app.get("/api/profile")
    async def profile():
        return {"notes": memory.recall("_hassan", 100), "skills": [
            {"name": n, "description": d} for n, d in orchestrator.operator.skills()]}

    @app.post("/api/profile", status_code=201)
    async def add_profile(note: ProfileNote):
        memory.remember("_hassan", "profile", note.content.strip()[:500])
        return {"ok": True}

    @app.post("/api/profile/forget")
    async def forget_profile(note: ProfileNote):
        return {"removed": memory.forget("_hassan", note.content)}

    @app.get("/api/whoami")
    async def whoami(request: Request):
        return {"local": request.state.local}

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    async def health():
        return {"name": "Hassan AI OS", "version": __version__, "mode": settings.mode,
                "agents": len(roster.agents), "gateway": settings.gateway_url if settings.mode == "live" else None}

    @app.get("/api/diagnostics")
    async def diagnostics():
        return readiness(settings, llm, desktop, mcp, len(orchestrator._jobs))

    def brain(alias: str) -> str:
        if isinstance(llm, RouterLLM):
            route = llm.route_for(alias)
            return route.backend + (f":{route.model}" if route.model else "")
        return "mock" if isinstance(llm, MockLLM) else alias

    @app.get("/api/agents")
    async def agents():
        rows = [{"name": a.name, "title": a.title, "model": a.model, "fallbacks": a.fallbacks}
                for a in roster.agents.values()]
        rows.append({"name": "cross_reviewer", "title": "المراجع المستقل", "model": roster.cross_reviewer,
                     "fallbacks": []})
        free = llm.free_brain() if isinstance(llm, RouterLLM) else None
        paid = llm.paid if isinstance(llm, RouterLLM) else set()
        return [{**r, "brain": brain(r["model"]), "free": free,
                 "paid": brain(r["model"]).split(":")[0] in paid} for r in rows]

    @app.get("/api/providers")
    async def providers():
        if isinstance(llm, RouterLLM):
            return {"mode": settings.mode, **await llm.status()}
        return {"mode": settings.mode, "backends": {"mock": {"type": "mock"}}}

    @app.post("/api/tasks", status_code=201)
    async def create_task(req: TaskCreate):
        if req.workspace:
            try:
                resolve_workspace(req.workspace, settings.allowed_roots)
            except PolicyError as exc:
                raise HTTPException(400, str(exc)) from exc
        return orchestrator.submit(req)

    @app.get("/api/tasks")
    async def list_tasks(limit: int = Query(30, le=200)):
        return [{"id": t.id, "prompt": t.prompt[:160], "status": t.status, "phase": t.phase,
                 "mode": t.resolved_mode or t.mode, "created_at": t.created_at, "verified": t.verified,
                 "conversation": t.conversation}
                for t in memory.list_tasks(limit)]

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        task = memory.get_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return {**task.model_dump(), "approvals": [a.model_dump() for a in memory.approvals(task_id)],
                "usage": memory.task_usage(task_id)}

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str, after: int = 0):
        return memory.events(task_id, after)

    @app.post("/api/tasks/{task_id}/rollback")
    async def rollback(task_id: str):
        try:
            return {"restored": await orchestrator.rollback(task_id)}
        except (KeyError, ValueError, PolicyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        try:
            orchestrator.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc
        return {"ok": True}

    # ---- live webcam / microphone (Hassan presses the button; one viewer each) --
    LIVE_TYPES = {"camera": "multipart/x-mixed-replace;boundary=ffmpeg", "mic": "audio/mpeg",
                  "screen": "multipart/x-mixed-replace;boundary=ffmpeg"}
    live: dict = {k: {"token": None, "expires": 0.0, "proc": None} for k in LIVE_TYPES}

    async def stop_live(kind: str) -> None:
        proc, live[kind]["proc"] = live[kind]["proc"], None
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()

    async def stop_camera() -> None:  # on shutdown: everything off
        for kind in LIVE_TYPES:
            await stop_live(kind)

    def live_kind(kind: str) -> str:
        if kind not in LIVE_TYPES:
            raise HTTPException(404, "Unknown live source")
        return kind

    @app.post("/api/live/{kind}")
    async def live_start(kind: str):
        # POST first (cross-site pages can't POST here), then the <img>/<audio> GETs the stream with the token
        slot = live[live_kind(kind)]
        slot["token"], slot["expires"] = secrets.token_urlsafe(18), time.time() + 60
        return {"url": f"/api/live/{kind}?token={slot['token']}"}

    @app.post("/api/live/{kind}/stop")
    async def live_stop(kind: str):
        await stop_live(live_kind(kind))
        return {"ok": True}

    @app.get("/api/live/{kind}")
    async def live_stream(kind: str, request: Request, token: str = ""):
        slot = live[live_kind(kind)]
        if not slot["token"] or time.time() > slot["expires"] or not secrets.compare_digest(token, slot["token"]):
            raise HTTPException(403, "Press the button again")
        slot["token"] = None
        await stop_live(kind)
        try:
            argv = await {"camera": pc.live_camera_argv, "mic": pc.live_mic_argv, "screen": pc.live_screen_argv}[kind]()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL,
                                                    stdin=asyncio.subprocess.DEVNULL, **NO_WINDOW)
        slot["proc"] = proc

        async def chunks():
            try:
                while chunk := await proc.stdout.read(16384):
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:  # the viewer closed the page or pressed stop: the device turns off
                if slot["proc"] is proc:
                    await stop_live(kind)
                elif proc.returncode is None:
                    proc.kill()

        return StreamingResponse(chunks(), media_type=LIVE_TYPES[kind], headers={"Cache-Control": "no-store"})

    AUDIO_EXT = {"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/webm": "webm"}

    async def audio_body(request: Request) -> tuple[bytes, str]:
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "No audio")
        if len(audio) > 20_000_000:
            raise HTTPException(413, "Recording too long")
        return audio, AUDIO_EXT.get(request.headers.get("content-type", "").split(";")[0].strip(), "webm")

    @app.post("/api/intercom/say")
    async def intercom_say(request: Request):
        """Hassan talks from his phone; the PC speakers play it (the other half of the line is /api/live/mic)."""
        audio, ext = await audio_body(request)
        try:
            return {"seconds": await pc.play_audio(audio, ext)}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/transcribe")
    async def transcribe(request: Request):
        """Voice from the dashboard (any browser) -> text, with Groq's free Whisper."""
        audio, ext = await audio_body(request)
        try:
            text = await groq_transcriber(audio, f"voice.{ext}")
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"text": text.strip()}

    @app.get("/api/media/{name}")
    async def media(name: str):
        """Photos, screenshots, recordings and renders made by the Operator."""
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(400, "Bad name")
        path = settings.data_dir / "media" / name
        if not path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/approvals")
    async def pending_approvals():
        return [a.model_dump() for a in memory.approvals(status="pending")]

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: Decision):
        try:
            return (await orchestrator.decide_approval(approval_id, body.approve, body.trust_similar)).model_dump()
        except KeyError as exc:
            raise HTTPException(404, "Approval not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/memory/{project}")
    async def recall(project: str):
        return memory.recall(project, 50)

    @app.post("/api/memory/{project}", status_code=201)
    async def remember(project: str, note: MemoryNote):
        memory.remember(project, note.kind, note.content)
        return {"ok": True}

    @app.get("/api/usage")
    async def usage():
        now = time.time()
        local = time.localtime(now)
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        return {"today": memory.usage_summary(midnight),
                "week": memory.usage_summary(now - 7 * 86400),
                "month": memory.usage_summary(now - 30 * 86400),
                "note": "cost_usd is the API list-price equivalent; subscription CLIs are not billed per call."}

    @app.get("/api/editor")
    async def editor_status():
        return {"command": settings.editor_command, "installed": shutil.which(settings.editor_command) is not None}

    @app.post("/api/open")
    async def open_in_editor(req: OpenRequest):
        """Open a file or folder in VS Code (only inside HASSAN_ALLOWED_ROOTS)."""
        target = Path(req.path).expanduser().resolve()
        if not any(target == r or r in target.parents for r in settings.allowed_roots):
            raise HTTPException(403, "Path is outside HASSAN_ALLOWED_ROOTS")
        if not target.exists():
            raise HTTPException(404, "Path not found")
        exe = shutil.which(settings.editor_command)
        if exe is None:
            raise HTTPException(503, f"'{settings.editor_command}' not found. In VS Code run: "
                                     "Shell Command: Install 'code' command in PATH")
        args = ["-g", f"{target}:{req.line}"] if (req.line and target.is_file()) else [str(target)]
        try:
            argv, env = editor_argv(exe, args)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        return {"opened": str(target)}

    @app.get("/api/models/stats")
    async def model_stats():
        return memory.model_stats()

    @app.get("/api/policy")
    async def get_policy():
        return {"auto": policy.auto, "approval": policy.approval, "forbidden": policy.forbidden,
                "allowed_roots": [str(r) for r in settings.allowed_roots]}

    @app.get("/api/mcp")
    async def mcp_status():
        return mcp.status()

    @app.get("/api/mcp/{server}/tools")
    async def mcp_tools(server: str):
        try:
            return await mcp.list_tools(server)
        except KeyError as exc:
            raise HTTPException(404, "Unknown MCP server") from exc
        except Exception as exc:  # noqa: BLE001 - connector errors are reported, not raised
            raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/mcp/{server}/tools/{tool}")
    async def mcp_call(server: str, tool: str, body: McpCall):
        # Public API: read-like tools only. Mutations must go through task approvals.
        try:
            return await mcp.call_tool(server, tool, body.arguments, approved=False)
        except PolicyError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Unknown MCP server") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc

    @app.get("/api/openhands")
    async def openhands_discover():
        return await openhands.discover(settings.openhands_url)

    return app


def main() -> None:
    from .cli import serve

    serve(Settings.from_env())


if __name__ == "__main__":
    main()
