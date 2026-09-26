"""FastAPI control plane + Arabic dashboard."""

from __future__ import annotations

import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, openhands
from .agents import Roster
from .config import PACKAGE_DIR, Settings
from .execution import CheckpointManager, ExecutionManager, SafeLocalRunner
from .llm import MockLLM
from .mcp_bus import MCPRegistry
from .memory import Memory
from .orchestrator import Orchestrator
from .policy import Policy, PolicyError, resolve_workspace
from .providers import RouterLLM
from .schemas import TaskCreate

STATIC = PACKAGE_DIR / "static"


class Decision(BaseModel):
    approve: bool


class McpCall(BaseModel):
    arguments: dict = {}


class OpenRequest(BaseModel):
    path: str
    line: int | None = None


class MemoryNote(BaseModel):
    kind: str = "note"
    content: str


BATCH_UNSAFE = set('%^&|<>"!')


def editor_argv(exe: str, args: list[str]) -> tuple[list[str], dict | None]:
    """On Windows `code` is a .cmd batch shim, where cmd.exe would interpret characters in
    paths. Launch Code.exe + cli.js directly (exactly what code.cmd does) to avoid cmd.exe."""
    import os

    if not exe.lower().endswith((".cmd", ".bat")):
        return [exe, *args], None
    bin_dir = Path(exe).parent
    code_exe = next((p for p in (bin_dir.parent / "Code.exe", bin_dir.parent / "Code - Insiders.exe") if p.exists()), None)
    cli_js = bin_dir.parent / "resources" / "app" / "out" / "cli.js"
    if code_exe and cli_js.exists():
        return [str(code_exe), str(cli_js), *args], {**os.environ, "ELECTRON_RUN_AS_NODE": "1"}
    if any(ch in BATCH_UNSAFE for a in args for ch in a):
        raise HTTPException(400, "Path contains characters that cannot be passed safely to code.cmd")
    return [exe, *args], None


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        orchestrator.recover_interrupted()
        yield
        await orchestrator.drain()
        if hasattr(llm, "aclose"):
            await llm.aclose()

    app = FastAPI(title="Hassan AI OS", version=__version__, lifespan=lifespan)
    app.state.orchestrator = orchestrator
    app.state.memory = memory

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Block DNS-rebinding (foreign Host) and cross-site requests from other web
        # pages (foreign Origin) — otherwise any website could approve changes.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in settings.allowed_hosts:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            if (urlsplit(origin).hostname or "") not in settings.allowed_hosts:
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    async def health():
        return {"name": "Hassan AI OS", "version": __version__, "mode": settings.mode,
                "agents": len(roster.agents), "gateway": settings.gateway_url if settings.mode == "live" else None}

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
        return [{**r, "brain": brain(r["model"])} for r in rows]

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
                 "mode": t.resolved_mode or t.mode, "created_at": t.created_at, "verified": t.verified}
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

    @app.get("/api/approvals")
    async def pending_approvals():
        return [a.model_dump() for a in memory.approvals(status="pending")]

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: Decision):
        try:
            return (await orchestrator.decide_approval(approval_id, body.approve)).model_dump()
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
        if not target.exists():
            raise HTTPException(404, "Path not found")
        if not any(target == r or r in target.parents for r in settings.allowed_roots):
            raise HTTPException(403, "Path is outside HASSAN_ALLOWED_ROOTS")
        exe = shutil.which(settings.editor_command)
        if exe is None:
            raise HTTPException(503, f"'{settings.editor_command}' not found. In VS Code run: "
                                     "Shell Command: Install 'code' command in PATH")
        args = ["-g", f"{target}:{req.line}"] if (req.line and target.is_file()) else [str(target)]
        argv, env = editor_argv(exe, args)
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
