import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from hassan_ai.config import POLICIES_DIR  # noqa: E402
from hassan_ai.mcp_bus import MCPRegistry  # noqa: E402
from hassan_ai.policy import Policy, PolicyError  # noqa: E402

SERVER = Path(__file__).resolve().parents[1] / "connectors" / "local-project-mcp" / "server.py"


def load_server_module():
    spec = importlib.util.spec_from_file_location("local_project_mcp", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_project_mcp_server(py_project):
    server = load_server_module().build_server(py_project)

    async def go():
        async with Client(server) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert names == {"inspect_project", "list_files", "read_file", "git_status", "verify"}
            info = json.loads((await c.call_tool("inspect_project", {})).content[0].text)
            assert "python" in info["kinds"]
            files = (await c.call_tool("list_files", {})).content[0].text
            assert "calc.py" in files and ".env" not in files
            denied = await c.call_tool("read_file", {"path": ".env"})
            assert denied.is_error
            escape = await c.call_tool("read_file", {"path": "../../etc/passwd"})
            assert escape.is_error

    asyncio.run(go())


def test_registry_blocks_mutating_tools(tmp_path):
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("servers:\n  blender:\n    transport: stdio\n    enabled: true\n    command: nothing\n")
    reg = MCPRegistry(cfg, Policy.load(POLICIES_DIR / "default.yaml"))
    with pytest.raises(PolicyError):
        asyncio.run(reg.call_tool("blender", "execute_python", {"code": "1"}))
    assert reg.status()["servers"][0]["name"] == "blender"
