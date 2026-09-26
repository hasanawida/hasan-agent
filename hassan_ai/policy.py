"""Permission policy (auto / approval / forbidden) and workspace containment."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_yaml

AUTO = "auto"
APPROVAL = "approval"
FORBIDDEN = "forbidden"

# MCP tools whose names look like reads are callable directly; anything else is
# treated as mutating and must go through an approval.
READ_VERB = re.compile(
    r"^(get|list|read|find|search|inspect|describe|show|status|diff|query|fetch|view|"
    r"diagnostics?|symbols?|outline|scene_info|ping|health)(_|$|[A-Z])",
    re.IGNORECASE,
)


class PolicyError(PermissionError):
    pass


@dataclass
class Policy:
    auto: list[str] = field(default_factory=list)
    approval: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    secret_globs: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        data = load_yaml(path)
        return cls(
            auto=data.get("auto", []),
            approval=data.get("approval", []),
            forbidden=data.get("forbidden", []),
            secret_globs=data.get("secret_files", []),
        )

    def decide(self, action: str) -> str:
        # Forbidden wins, then explicit approval, then auto. Unknown actions need approval.
        for bucket, name in ((self.forbidden, FORBIDDEN), (self.approval, APPROVAL), (self.auto, AUTO)):
            if any(fnmatch.fnmatchcase(action, pat) for pat in bucket):
                return name
        return APPROVAL

    def decide_mcp(self, server: str, tool: str) -> str:
        name = f"mcp.{server}.{tool}"
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.forbidden):
            return FORBIDDEN
        # An exact per-tool entry beats the generic mcp.*.* rule.
        if name in self.auto:
            return AUTO
        if name in self.approval:
            return APPROVAL
        return AUTO if READ_VERB.match(tool) else APPROVAL

    def is_secret(self, rel_path: str) -> bool:
        name = rel_path.replace("\\", "/")
        base = name.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(base, g) or fnmatch.fnmatch(name, g) for g in self.secret_globs)


def resolve_workspace(raw: str, allowed_roots: list[Path]) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise PolicyError(f"Workspace does not exist or is not a directory: {path}")
    if not any(path == root or root in path.parents for root in allowed_roots):
        roots = ", ".join(str(r) for r in allowed_roots)
        raise PolicyError(f"Workspace {path} is outside HASSAN_ALLOWED_ROOTS ({roots})")
    return path


def resolve_in_workspace(workspace: Path, rel: str) -> Path:
    """Resolve *rel* inside *workspace*, rejecting traversal and symlink escapes."""
    if not rel or rel.strip() in {".", ""}:
        raise PolicyError("Empty path")
    candidate = Path(rel)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (workspace / candidate).resolve()
    if target != workspace and workspace not in target.parents:
        raise PolicyError(f"Path escapes workspace: {rel}")
    if ".git" in target.relative_to(workspace).parts:
        raise PolicyError("Direct access to .git internals is not allowed")
    return target
