import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hassan_ai.config import CONFIGS_DIR, POLICIES_DIR, Settings  # noqa: E402
from hassan_ai.server import create_app  # noqa: E402

TERMINAL = {"completed", "failed", "rejected", "awaiting_approval"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        mode="mock",
        data_dir=tmp_path / "data",
        allowed_roots=[tmp_path.resolve()],
        policy_file=POLICIES_DIR / "default.yaml",
        mcp_config=CONFIGS_DIR / "mcp_servers.yaml",
        agents_config=CONFIGS_DIR / "agents.yaml",
        command_timeout=120,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
        trusted_clients=["127.0.0.1", "::1", "testclient"],
        env_file=tmp_path / "hassan.env",
    )


@pytest.fixture
def app_factory(tmp_path):
    def factory(llm=None):
        return TestClient(create_app(make_settings(tmp_path), llm=llm))
    return factory


@pytest.fixture
def client(app_factory):
    with app_factory() as c:
        yield c


@pytest.fixture
def py_project(tmp_path):
    """A tiny git-tracked Python project with a passing test."""
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.0.1"\n')
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(textwrap.dedent("""
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from calc import add

        def test_add():
            assert add(2, 3) == 5
    """))
    (root / ".env").write_text("SECRET=1\n")
    run = lambda *a: subprocess.run(a, cwd=root, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def wait(client, task_id, statuses=TERMINAL, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in statuses:
            return task
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} stuck in {task['status']}/{task['phase']}")
