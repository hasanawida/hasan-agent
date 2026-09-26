"""Hassan Local Project MCP server.

Exposes read/verify access to one workspace so any MCP host (Claude, Codex,
Grok via custom MCP, VS Code …) sees the same project Hassan sees. It reuses the
same policy and containment rules as the core; there is no write or shell tool.

    HASSAN_MCP_WORKSPACE=/path/to/project python connectors/local-project-mcp/server.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from hassan_ai.config import Settings  # noqa: E402
from hassan_ai.execution import CheckpointManager, ExecutionManager, SafeLocalRunner  # noqa: E402
from hassan_ai.policy import Policy  # noqa: E402


def build_server(workspace: Path) -> MCPServer:
    settings = Settings.from_env()
    policy = Policy.load(settings.policy_file)
    runner = SafeLocalRunner(settings.command_timeout)
    execution = ExecutionManager(policy, runner, CheckpointManager(settings.checkpoints_dir, runner))
    server = MCPServer("hassan-local-project", instructions=f"Read/verify tools for {workspace}")

    @server.tool()
    def inspect_project() -> str:
        """Detect project type, build/test commands and top-level layout."""
        info, _ = execution.inspect(workspace)
        return json.dumps(info.as_dict(), ensure_ascii=False, indent=2)

    @server.tool()
    def list_files(limit: int = 400) -> str:
        """List workspace files (skips build output and secrets)."""
        return "\n".join(execution.list_files(workspace, min(limit, 2000)))

    @server.tool()
    def read_file(path: str) -> str:
        """Read a text file relative to the workspace."""
        return execution.read_file(workspace, path)

    @server.tool()
    async def git_status() -> str:
        """git status and diff --stat as evidence."""
        return json.dumps([e.model_dump() for e in await execution.git_evidence(workspace)], ensure_ascii=False)

    @server.tool()
    async def verify(kind: str = "test") -> str:
        """Run the detected allow-listed build or test commands (kind: build|test)."""
        if kind not in ("build", "test"):
            raise ValueError("kind must be build or test")
        info, _ = execution.inspect(workspace)
        return json.dumps([e.model_dump() for e in await execution.verify(workspace, info, (kind,))], ensure_ascii=False)

    return server


def main() -> None:
    raw = os.environ.get("HASSAN_MCP_WORKSPACE") or os.getcwd()
    workspace = Path(raw).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace not found: {workspace}")
    build_server(workspace).run()


if __name__ == "__main__":
    main()
