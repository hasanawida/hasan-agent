"""Runtime settings for Hassan AI OS.

Everything is read from environment variables so the same code runs on a
developer laptop (mock mode, no keys) and in live multi-model mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
CONFIGS_DIR = ROOT_DIR / "configs"
POLICIES_DIR = ROOT_DIR / "policies"


def _env_list(name: str, default: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, default).split(os.pathsep) if p.strip()]


@dataclass
class Settings:
    mode: str = "mock"  # mock | live
    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: Path = ROOT_DIR / "data"
    gateway_url: str = "http://127.0.0.1:4000/v1"
    gateway_key: str = ""
    request_timeout: float = 180.0
    allowed_roots: list[Path] = field(default_factory=list)
    policy_file: Path = POLICIES_DIR / "default.yaml"
    mcp_config: Path = CONFIGS_DIR / "mcp_servers.yaml"
    agents_config: Path = CONFIGS_DIR / "agents.yaml"
    providers_config: Path = CONFIGS_DIR / "providers.yaml"
    openhands_url: str = "http://127.0.0.1:3000"
    max_repair_rounds: int = 2
    command_timeout: float = 900.0
    editor_command: str = "code"
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hassan.db"

    @property
    def checkpoints_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file(ROOT_DIR / "hassan.env")
        data_dir = Path(os.environ.get("HASSAN_DATA_DIR", ROOT_DIR / "data")).resolve()
        roots = _env_list("HASSAN_ALLOWED_ROOTS", str(Path.home()))
        return cls(
            mode=os.environ.get("HASSAN_AI_MODE", "mock").lower(),
            host=os.environ.get("HASSAN_HOST", "127.0.0.1"),
            port=int(os.environ.get("HASSAN_PORT", "8787")),
            data_dir=data_dir,
            gateway_url=os.environ.get("HASSAN_GATEWAY_URL", "http://127.0.0.1:4000/v1").rstrip("/"),
            gateway_key=os.environ.get("HASSAN_GATEWAY_KEY", os.environ.get("LITELLM_MASTER_KEY", "")),
            request_timeout=float(os.environ.get("HASSAN_REQUEST_TIMEOUT", "180")),
            allowed_roots=[Path(r).expanduser().resolve() for r in roots],
            policy_file=Path(os.environ.get("HASSAN_POLICY_FILE", POLICIES_DIR / "default.yaml")),
            mcp_config=Path(os.environ.get("HASSAN_MCP_CONFIG", CONFIGS_DIR / "mcp_servers.yaml")),
            agents_config=Path(os.environ.get("HASSAN_AGENTS_CONFIG", CONFIGS_DIR / "agents.yaml")),
            providers_config=Path(os.environ.get("HASSAN_PROVIDERS_CONFIG", CONFIGS_DIR / "providers.yaml")),
            openhands_url=os.environ.get("HASSAN_OPENHANDS_URL", "http://127.0.0.1:3000").rstrip("/"),
            max_repair_rounds=int(os.environ.get("HASSAN_MAX_REPAIR_ROUNDS", "2")),
            command_timeout=float(os.environ.get("HASSAN_COMMAND_TIMEOUT", "900")),
            editor_command=os.environ.get("HASSAN_EDITOR", "code"),
        )


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines (written by the Windows installer). Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
