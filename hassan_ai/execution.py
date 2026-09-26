"""Evidence + execution layer.

* ProjectDetector   – figures out what kind of project a workspace is.
* SafeLocalRunner   – runs a fixed allow-list of commands (no shell, ever).
* CheckpointManager – non-destructive snapshot before execution, per-file backups.
* ExecutionManager  – ties them together and produces Evidence records.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .policy import AUTO, FORBIDDEN, Policy, PolicyError, resolve_in_workspace
from .schemas import Evidence, FileChange

MAX_OUTPUT = 12_000
SKIP_DIRS = {".git", "node_modules", "bin", "obj", ".venv", "venv", "__pycache__", ".vs", "dist", "build",
             "Library", "Temp", ".idea", ".pytest_cache", "packages"}


def _tail(text: str, limit: int = MAX_OUTPUT) -> str:
    return text if len(text) <= limit else "…\n" + text[-limit:]


@dataclass
class ProjectInfo:
    root: str
    kinds: list[str] = field(default_factory=list)
    git: bool = False
    solutions: list[str] = field(default_factory=list)
    blend_files: list[str] = field(default_factory=list)
    build: list[list[str]] = field(default_factory=list)
    test: list[list[str]] = field(default_factory=list)
    file_count: int = 0
    top_level: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class ProjectDetector:
    def detect(self, root: Path) -> ProjectInfo:
        info = ProjectInfo(root=str(root))
        info.git = (root / ".git").exists()
        info.top_level = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))[:60]

        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth > 4:
                dirnames[:] = []
            for name in filenames:
                files.append(Path(dirpath) / name)
            if len(files) > 20_000:
                break
        info.file_count = len(files)
        rel = lambda p: str(p.relative_to(root))  # noqa: E731

        sln = [f for f in files if f.suffix == ".sln" or f.suffix == ".slnx"]
        csproj = [f for f in files if f.suffix in {".csproj", ".vcxproj", ".fsproj"}]
        if sln or csproj:
            info.kinds.append("dotnet")
            info.solutions = [rel(f) for f in (sln or csproj)][:10]
            target = info.solutions[0]
            info.build.append(["dotnet", "build", target, "-nologo", "-v", "minimal"])
            if any("test" in f.name.lower() for f in csproj):
                info.test.append(["dotnet", "test", target, "-nologo", "-v", "minimal"])

        if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
            info.kinds.append("python")
            if (root / "tests").is_dir() or any(f.name.startswith("test_") and f.suffix == ".py" for f in files):
                info.test.append([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
            else:
                info.build.append([sys.executable, "-m", "compileall", "-q", "."])

        pkg = root / "package.json"
        if pkg.exists():
            info.kinds.append("node")
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            except (ValueError, OSError):
                scripts = {}
            npm = "npm.cmd" if os.name == "nt" else "npm"
            if "build" in scripts:
                info.build.append([npm, "run", "build"])
            if "test" in scripts and "no test specified" not in scripts["test"]:
                info.test.append([npm, "test", "--silent"])

        if (root / "Cargo.toml").exists():
            info.kinds.append("rust")
            info.build.append(["cargo", "build"])
            info.test.append(["cargo", "test"])

        if (root / "go.mod").exists():
            info.kinds.append("go")
            info.build.append(["go", "build", "./..."])
            info.test.append(["go", "test", "./..."])

        if (root / "ProjectSettings" / "ProjectVersion.txt").exists():
            info.kinds.append("unity")

        blends = [f for f in files if f.suffix == ".blend"]
        if blends:
            info.kinds.append("blender")
            info.blend_files = [rel(f) for f in blends][:20]

        if not info.kinds:
            info.kinds.append("generic")
        return info


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SafeLocalRunner:
    """Executes only argv lists produced by this module. There is no shell tool."""

    ALLOWED_EXECUTABLES = {"git", "dotnet", "msbuild", "npm", "npm.cmd", "cargo", "go",
                           Path(sys.executable).name, "python", "python3", "pytest"}

    def __init__(self, timeout: float = 900.0):
        self.timeout = timeout

    async def run(self, argv: list[str], cwd: Path, timeout: float | None = None) -> CommandResult:
        exe = Path(argv[0]).name
        if exe not in self.ALLOWED_EXECUTABLES and argv[0] != sys.executable:
            raise PolicyError(f"Executable not allow-listed: {argv[0]}")
        if shutil.which(argv[0]) is None and not Path(argv[0]).exists():
            return CommandResult(argv, 127, "", f"{argv[0]} not found on PATH", 0.0)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "CI": "1"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout or self.timeout)
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            timed_out = True
        return CommandResult(
            argv, proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
            time.monotonic() - start, timed_out,
        )


class CheckpointManager:
    def __init__(self, base: Path, runner: SafeLocalRunner):
        self.base = base
        self.runner = runner

    async def create(self, task_id: str, workspace: Path, is_git: bool) -> dict:
        folder = self.base / task_id
        folder.mkdir(parents=True, exist_ok=True)
        meta: dict = {"task_id": task_id, "workspace": str(workspace), "created_at": time.time(),
                      "folder": str(folder), "git": is_git, "backups": []}
        if is_git:
            head = await self.runner.run(["git", "rev-parse", "HEAD"], workspace, 30)
            meta["head"] = head.stdout.strip() if head.ok else None
            # `git stash create` snapshots the dirty tree into a commit object
            # without touching the working copy or the stash list.
            stash = await self.runner.run(["git", "stash", "create"], workspace, 60)
            meta["worktree_snapshot"] = stash.stdout.strip() or None
        self._write(folder, meta)
        return meta

    def backup_file(self, meta: dict, workspace: Path, target: Path) -> None:
        folder = Path(meta["folder"])
        rel = target.relative_to(workspace)
        entry = {"path": str(rel), "existed": target.exists()}
        if target.exists():
            dest = folder / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest)
        if all(b["path"] != entry["path"] for b in meta["backups"]):
            meta["backups"].append(entry)
        self._write(folder, meta)

    def restore(self, meta: dict) -> list[str]:
        workspace = Path(meta["workspace"])
        folder = Path(meta["folder"])
        restored = []
        for entry in meta.get("backups", []):
            target = resolve_in_workspace(workspace, entry["path"])
            if entry["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / "files" / entry["path"], target)
            elif target.exists():
                target.unlink()
            restored.append(entry["path"])
        return restored

    @staticmethod
    def _write(folder: Path, meta: dict) -> None:
        (folder / "checkpoint.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


class ExecutionManager:
    def __init__(self, policy: Policy, runner: SafeLocalRunner, checkpoints: CheckpointManager):
        self.policy = policy
        self.runner = runner
        self.checkpoints = checkpoints
        self.detector = ProjectDetector()

    def _require(self, action: str) -> None:
        decision = self.policy.decide(action)
        if decision == FORBIDDEN:
            raise PolicyError(f"Action forbidden by policy: {action}")
        if decision != AUTO:
            raise PolicyError(f"Action {action} requires approval")

    # --- read-only evidence ----------------------------------------------
    def inspect(self, workspace: Path) -> tuple[ProjectInfo, Evidence]:
        self._require("project.inspect")
        info = self.detector.detect(workspace)
        summary = f"{', '.join(info.kinds)} · {info.file_count} files · git={'yes' if info.git else 'no'}"
        return info, Evidence(kind="inspect", title="Project detection", ok=True, summary=summary,
                              detail=json.dumps(info.as_dict(), indent=2, ensure_ascii=False))

    def read_file(self, workspace: Path, rel: str, limit: int = 60_000) -> str:
        self._require("files.read")
        if self.policy.is_secret(rel):
            raise PolicyError(f"Secret file access denied: {rel}")
        target = resolve_in_workspace(workspace, rel)
        return target.read_text(encoding="utf-8", errors="replace")[:limit]

    def list_files(self, workspace: Path, limit: int = 400) -> list[str]:
        self._require("files.list")
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(workspace):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                rel = str((Path(dirpath) / name).relative_to(workspace))
                if not self.policy.is_secret(rel):
                    out.append(rel)
                if len(out) >= limit:
                    return out
        return out

    async def git_evidence(self, workspace: Path) -> list[Evidence]:
        self._require("git.status")
        self._require("git.diff")
        status = await self.runner.run(["git", "status", "--porcelain=v1", "--branch"], workspace, 60)
        stat = await self.runner.run(["git", "diff", "--stat"], workspace, 60)
        changed = [line for line in status.stdout.splitlines() if not line.startswith("##")]
        return [
            Evidence(kind="git_status", title="git status", ok=status.ok,
                     summary=f"{len(changed)} changed path(s)" if status.ok else "git status failed",
                     detail=_tail(status.stdout + status.stderr), command=status.command,
                     exit_code=status.exit_code, duration=status.duration),
            Evidence(kind="git_diff", title="git diff --stat", ok=stat.ok,
                     summary=(stat.stdout.strip().splitlines() or ["no unstaged diff"])[-1],
                     detail=_tail(stat.stdout + stat.stderr), command=stat.command,
                     exit_code=stat.exit_code, duration=stat.duration),
        ]

    async def verify(self, workspace: Path, info: ProjectInfo, which: tuple[str, ...] = ("build", "test")) -> list[Evidence]:
        evidence: list[Evidence] = []
        for kind in which:
            self._require(f"verify.{kind}")
            for argv in getattr(info, kind):
                res = await self.runner.run(argv, workspace)
                status = "timed out" if res.timed_out else f"exit {res.exit_code}"
                last = (res.stdout.strip() or res.stderr.strip()).splitlines()[-1:] or [""]
                evidence.append(Evidence(
                    kind=kind, title=" ".join(Path(argv[0]).name if i == 0 else a for i, a in enumerate(argv)),
                    ok=res.ok, summary=f"{status} · {last[0][:160]}",
                    detail=_tail(res.stdout + ("\n[stderr]\n" + res.stderr if res.stderr else "")),
                    command=argv, exit_code=res.exit_code, duration=round(res.duration, 2)))
        return evidence

    # --- mutations (only called after an approval) ------------------------
    def preview_change(self, workspace: Path, change: FileChange) -> str:
        if self.policy.is_secret(change.path):
            raise PolicyError(f"Secret file access denied: {change.path}")
        target = resolve_in_workspace(workspace, change.path)
        before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        after = "" if change.action == "delete" else change.content
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{change.path}", tofile=f"b/{change.path}"))

    def apply_change(self, workspace: Path, change: FileChange, checkpoint: dict) -> Evidence:
        if self.policy.decide(f"files.{change.action}") == FORBIDDEN or self.policy.is_secret(change.path):
            raise PolicyError(f"Change not permitted: {change.path}")
        target = resolve_in_workspace(workspace, change.path)
        self.checkpoints.backup_file(checkpoint, workspace, target)
        if change.action == "delete":
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
        return Evidence(kind="write", title=f"{change.action} {change.path}", ok=True,
                        summary=change.reason or "applied after approval")
