"""MCP connector registry (Visual Studio, Blender, local project, …).

Servers are declared in ``configs/mcp_servers.yaml`` as either ``stdio``
subprocesses or Streamable HTTP URLs. The MCP Python SDK is optional: when it
is not installed the registry still loads and reports the connectors as
unavailable instead of crashing the app.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_yaml
from .policy import AUTO, FORBIDDEN, Policy, PolicyError

try:  # optional dependency: pip install -e ".[mcp]"
    from mcp import Client, StdioServerParameters

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    Client = StdioServerParameters = None  # type: ignore[assignment]
    MCP_AVAILABLE = False


@dataclass
class ServerSpec:
    name: str
    transport: str  # stdio | http
    enabled: bool = False
    description: str = ""
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None

    def public(self) -> dict:
        return {"name": self.name, "transport": self.transport, "enabled": self.enabled,
                "description": self.description, "target": self.url or self.command}


class MCPRegistry:
    def __init__(self, config_path: Path, policy: Policy, timeout: float = 60.0):
        self.policy = policy
        self.timeout = timeout
        self.servers: dict[str, ServerSpec] = {}
        # Local switch (hassan.env) so enabling a connector never edits the shared config file.
        extra = {n.strip() for n in os.environ.get("HASSAN_MCP_ENABLE", "").split(",") if n.strip()}
        for name, cfg in (load_yaml(config_path).get("servers") or {}).items():
            self.servers[name] = ServerSpec(
                name=name, transport=cfg.get("transport", "stdio"),
                enabled=bool(cfg.get("enabled", False)) or name in extra,
                description=cfg.get("description", ""), command=cfg.get("command"),
                args=[os.path.expandvars(a) for a in cfg.get("args", [])],
                env={k: os.path.expandvars(str(v)) for k, v in (cfg.get("env") or {}).items()},
                url=cfg.get("url"))

    def status(self) -> dict:
        return {"sdk_installed": MCP_AVAILABLE, "servers": [s.public() for s in self.servers.values()]}

    def _client(self, name: str):
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP SDK not installed. Run: pip install -e \".[mcp]\"")
        spec = self.servers.get(name)
        if spec is None:
            raise KeyError(name)
        if not spec.enabled:
            raise RuntimeError(f"MCP server '{name}' is disabled in configs/mcp_servers.yaml")
        if spec.transport == "http":
            return Client(spec.url, read_timeout_seconds=self.timeout)
        params = StdioServerParameters(command=spec.command, args=spec.args, env={**os.environ, **spec.env})
        return Client(params, read_timeout_seconds=self.timeout)

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        async with self._client(name) as client:
            result = await client.list_tools()
        return [{"name": t.name, "description": t.description or "",
                 "access": self.policy.decide_mcp(name, t.name)} for t in result.tools]

    async def call_tool(self, name: str, tool: str, arguments: dict[str, Any], *, approved: bool = False) -> dict:
        decision = self.policy.decide_mcp(name, tool)
        if decision == FORBIDDEN:
            raise PolicyError(f"MCP tool forbidden: {name}.{tool}")
        if decision != AUTO and not approved:
            raise PolicyError(f"MCP tool {name}.{tool} is mutating and requires approval")
        async with self._client(name) as client:
            result = await client.call_tool(tool, arguments)
        texts = [getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text"]
        return {"is_error": bool(result.is_error), "text": "\n".join(texts),
                "structured": getattr(result, "structured_content", None)}
