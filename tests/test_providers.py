"""Live-mode routing through the Claude Code / Codex CLIs, using fake binaries."""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from hassan_ai.providers import RouterLLM
from hassan_ai.server import create_app
from fastapi.testclient import TestClient

from .conftest import make_settings, wait

ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = """
import json, os, sys, asyncio
sys.path.insert(0, {root!r})
from hassan_ai.llm import MockLLM
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
assert "-p" in args and args[args.index("--tools") + 1] == ""
system = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read()
user = sys.stdin.read()
with open(os.environ["FAKE_LOG"] + "." + str(os.getpid()) + ".json", "w", encoding="utf-8") as log:
    log.write(json.dumps({{"cli": "claude", "cwd": os.getcwd(), "key": os.environ.get("ANTHROPIC_API_KEY")}}) + "\\n")
text = asyncio.run(MockLLM().complete("claude", system, user)).text
print(json.dumps({{"type": "result", "is_error": False, "result": text, "total_cost_usd": 0.01,
                  "usage": {{"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20}},
                  "modelUsage": {{"claude-opus-5-5": {{"inputTokens": 100}}}}}}))
"""

FAKE_CODEX = """
import json, os, sys, asyncio, re
sys.path.insert(0, {root!r})
from hassan_ai.llm import MockLLM
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if args == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
assert args[0] == "exec" and args[args.index("--sandbox") + 1] == "read-only" and args[-1] == "-"
prompt = sys.stdin.read()
system = re.search(r"<instructions>\\n(.*?)\\n</instructions>", prompt, re.S).group(1)
with open(os.environ["FAKE_LOG"] + "." + str(os.getpid()) + ".json", "w", encoding="utf-8") as log:
    log.write(json.dumps({{"cli": "codex", "cwd": os.getcwd(), "key": os.environ.get("OPENAI_API_KEY")}}) + "\\n")
text = asyncio.run(MockLLM().complete("chatgpt", system, prompt)).text
open(args[args.index("-o") + 1], "w", encoding="utf-8").write(text)
print(json.dumps({{"type": "thread.started"}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 70, "cached_input_tokens": 10, "output_tokens": 9}}}}))
"""


def make_cli(folder: Path, name: str, body: str) -> Path:
    script = folder / (name + ".py" if os.name == "nt" else name)
    script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body.format(root=str(ROOT))), encoding="utf-8")
    script.chmod(0o755)
    if os.name == "nt":
        wrapper = folder / (name + ".cmd")
        wrapper.write_text(f'@echo off\nchcp 65001 >nul\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return wrapper
    return script


@pytest.fixture
def fake_clis(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = make_cli(bindir, "claude", FAKE_CLAUDE)
    codex = make_cli(bindir, "codex", FAKE_CODEX)
    log = tmp_path / "cli.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-hidden")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-hidden")
    return claude, codex, log


def write_providers(tmp_path, claude_cmd, codex_cmd) -> Path:
    claude_cmd, codex_cmd = Path(claude_cmd).as_posix(), Path(codex_cmd).as_posix()
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        backends:
          claude: {{type: claude_cli, command: "{claude_cmd}"}}
          chatgpt: {{type: codex_cli, command: "{codex_cmd}"}}
        default: claude
        aliases:
          coder: claude
          coder-backup: chatgpt
          coder-third: chatgpt
          reviewer: chatgpt
          cross-reviewer: claude
          decision: chatgpt
          general: chatgpt
    """))
    return cfg


def live_client(tmp_path, providers_cfg):
    settings = make_settings(tmp_path)
    settings.mode = "live"
    settings.providers_config = providers_cfg
    return TestClient(create_app(settings))


def test_claude_and_chatgpt_work_together(tmp_path, fake_clis, py_project):
    claude, codex, log = fake_clis
    with live_client(tmp_path, write_providers(tmp_path, claude, codex)) as client:
        agents = {a["name"]: a["brain"] for a in client.get("/api/agents").json()}
        assert agents["coder"] == "claude" and agents["reviewer"] == "chatgpt"

        status = client.get("/api/providers").json()
        assert status["backends"]["claude"]["installed"]
        assert "ChatGPT" in status["backends"]["chatgpt"]["login status"]

        t = client.post("/api/tasks", json={"prompt": "critical: review the calculator", "mode": "consensus", "budget": "best",
                                            "workspace": str(py_project), "execute": True}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        models = {o["agent"]: o["model"] for o in task["outputs"]}
        assert models["manager"].startswith("claude_cli")
        assert models["reviewer"].startswith("codex_cli")
        assert models["cross_reviewer"].startswith("claude_cli")
        manager = next(o for o in task["outputs"] if o["agent"] == "manager")
        assert manager["model"] == "claude_cli/claude-opus-5-5"
        assert (manager["input_tokens"], manager["output_tokens"], manager["cost_usd"]) == (150, 20, 0.01)
        reviewer = next(o for o in task["outputs"] if o["agent"] == "reviewer")
        assert (reviewer["input_tokens"], reviewer["output_tokens"]) == (70, 9)
        assert task["usage"]["calls"] == len(task["outputs"])
        coders = [o["model"] for o in task["outputs"] if o["agent"] == "coder"]
        assert any(m.startswith("claude_cli") for m in coders) and any(m.startswith("codex_cli") for m in coders)

    # Parallel fake CLIs must not append to the same Windows text stream.
    calls = [json.loads(p.read_text(encoding="utf-8")) for p in log.parent.glob(log.name + ".*.json")]
    assert {c["cli"] for c in calls} == {"claude", "codex"}
    # subscription mode: API keys are not passed to the CLIs
    assert all(c["key"] is None for c in calls)
    # the CLIs never run inside the user's project
    assert all(Path(c["cwd"]).resolve() != py_project.resolve() for c in calls)


def test_missing_claude_falls_back_to_chatgpt(tmp_path, fake_clis):
    _, codex, _ = fake_clis
    cfg = write_providers(tmp_path, tmp_path / "bin" / "not-installed", codex)
    with live_client(tmp_path, cfg) as client:
        status = client.get("/api/providers").json()
        assert status["backends"]["claude"]["installed"] is False
        t = client.post("/api/tasks", json={"prompt": "plan something", "mode": "fast", "budget": "best"}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        manager = next(o for o in task["outputs"] if o["agent"] == "manager")
        assert manager["model"].startswith("codex_cli")
        events = client.get(f"/api/tasks/{t['id']}/events").json()
        assert any(e["kind"] == "fallback" and "not found" in e["data"]["error"] for e in events)


def test_router_rejects_unknown_backend(tmp_path):
    cfg = tmp_path / "p.yaml"
    cfg.write_text("backends:\n  a: {type: mock}\ndefault: a\naliases:\n  coder: nope\n")
    with pytest.raises(ValueError):
        RouterLLM.from_config(cfg, "http://x", "", 10)


def test_default_providers_config_loads():
    router = RouterLLM.from_config(ROOT / "configs" / "providers.yaml", "http://127.0.0.1:4000/v1", "", 10)
    assert router.route_for("coder").backend == "claude"
    assert router.route_for("reviewer").backend == "chatgpt"
    assert router.route_for("unknown-alias").backend == "claude"


def test_usage_limit_is_remembered_and_skipped(tmp_path, fake_clis):
    """Claude's weekly-limit answer: fall back to ChatGPT at once and show it in the status."""
    _, codex, log = fake_clis
    limited = make_cli(tmp_path / "bin", "claude-limited", """
import json, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
open({log!r}, "a").write('{{"cli": "claude-limited"}}\\n')
print(json.dumps({{"type": "result", "is_error": True, "api_error_status": 429,
                  "result": "You've hit your weekly limit · resets Sep 29, 4pm"}}))
sys.exit(1)
""".replace("{log!r}", repr(str(log))))
    with live_client(tmp_path, write_providers(tmp_path, limited, codex)) as client:
        t = client.post("/api/tasks", json={"prompt": "x", "mode": "fast", "budget": "best"}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        assert all(o["model"].startswith("codex_cli") for o in task["outputs"])
        status = client.get("/api/providers").json()["backends"]["claude"]
        assert "weekly limit" in status["limit"]
    calls = [line for line in log.read_text().splitlines() if "claude-limited" in line]
    assert len(calls) == 1  # tried once, then skipped for the rest of the task


FAKE_GEMINI = """
import json, os, sys, asyncio, re
sys.path.insert(0, {root!r})
from hassan_ai.llm import MockLLM
args = sys.argv[1:]
if args == ["--version"]:
    print("0.61.0"); sys.exit(0)
assert "-p" in args and args[args.index("-o") + 1] == "json" and args[args.index("--approval-mode") + 1] == "plan"
assert os.environ.get("GEMINI_API_KEY") is None  # subscription/login mode
prompt = sys.stdin.read()
system = re.search(r"<instructions>\\n(.*?)\\n</instructions>", prompt, re.S).group(1)
text = asyncio.run(MockLLM().complete("gemini", system, prompt)).text
print('Approval mode overridden to "default" because the current folder is not trusted.')
print(json.dumps({{"session_id": "s", "response": text,
                  "stats": {{"models": {{"gemini-3.5-pro": {{"tokens": {{"prompt": 40, "candidates": 6, "total": 46}}}}}}}}}}, indent=2))
"""


def test_gemini_cli_brain(tmp_path, fake_clis, monkeypatch):
    claude, codex, _ = fake_clis
    gemini = make_cli(tmp_path / "bin", "gemini", FAKE_GEMINI)
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-reach-cli")
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        backends:
          claude: {{type: claude_cli, command: "{claude.as_posix()}"}}
          chatgpt: {{type: codex_cli, command: "{codex.as_posix()}"}}
          gemini-sub: {{type: gemini_cli, command: "{gemini.as_posix()}"}}
        default: gemini-sub
    """))
    with live_client(tmp_path, cfg) as client:
        t = client.post("/api/tasks", json={"prompt": "research", "mode": "fast", "budget": "best"}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        out = task["outputs"][0]
        assert out["model"] == "gemini_cli/gemini-3.5-pro"
        assert (out["input_tokens"], out["output_tokens"]) == (40, 6)
        assert client.get("/api/providers").json()["backends"]["gemini-sub"]["installed"]


def test_gemini_quota_error_marks_limit():
    from hassan_ai.providers import CLIBackend
    b = CLIBackend(kind="gemini_cli", command="gemini")
    with pytest.raises(Exception):
        b._parse_gemini('{"error": {"message": "Quota exceeded: RESOURCE_EXHAUSTED", "code": 429}}', "", 1, None)
    assert "Quota" in b.limit_message
