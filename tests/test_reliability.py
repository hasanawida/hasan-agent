"""Real cancellation, honest outcomes, and observable file-effect checks."""
import asyncio
import json
import os
import sys
import threading
import time

import psutil
import pytest

from hassan_ai.processes import run_process
from hassan_ai.verification import check_file_result, result_ok
from .conftest import wait
from .test_operator import op, pending, start


@pytest.mark.parametrize("cancel", [False, True])
def test_process_tree_stops_before_returning(tmp_path, cancel):
    ready = tmp_path / "ready.json"
    marker = tmp_path / "must-not-be-written.txt"
    child = "import time,pathlib;time.sleep(3);pathlib.Path(" + repr(str(marker)) + ").write_text('leaked');time.sleep(30)"
    parent = ("import subprocess,sys,pathlib,json,time;"
              "p=subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);"
              "pathlib.Path(" + repr(str(ready)) + ").write_text(json.dumps(p.pid));time.sleep(40)")

    async def scenario():
        job = asyncio.create_task(run_process([sys.executable, "-c", parent], timeout=1.5 if not cancel else 30))
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(.01)
        assert ready.exists()
        pid = json.loads(ready.read_text())
        if cancel:
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        else:
            assert (await job).timed_out
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        assert not marker.exists()
    asyncio.run(scenario())


def test_cancelling_file_worker_waits_until_it_finishes(client, tmp_path, monkeypatch):
    tools = client.app.state.orchestrator.operator.tools
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = tools._t_write_file
    def slow_write(**args):
        entered.set()
        assert release.wait(10)
        value = original(**args)
        finished.set()
        return value
    monkeypatch.setattr(tools, "_t_write_file", slow_write)
    t = start(client, op("write_file", path=str(tmp_path / "note.txt"), content="hello"))
    _, approval = pending(client, t["id"])
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True})
    try:
        assert entered.wait(5)
        client.post(f"/api/tasks/{t['id']}/cancel")
        assert client.get(f"/api/tasks/{t['id']}").json()["status"] == "cancelling"
        client.post(f"/api/tasks/{t['id']}/cancel")  # repeated clicks do not interrupt cleanup
    finally:
        release.set()
    task = wait(client, t["id"], {"cancelled"})
    assert finished.is_set() and task["status"] == "cancelled"
    assert not client.app.state.orchestrator._jobs


def test_exhausted_step_budget_is_incomplete(client, monkeypatch):
    from hassan_ai import operator_mode
    monkeypatch.setattr(operator_mode, "MAX_STEPS", 1)
    t = start(client, op("system_info"))
    assert wait(client, t["id"], {"completed", "failed"})["status"] == "incomplete"


@pytest.mark.parametrize("tool,result", [("run", "exit 01\nno"), ("run", "timed out after 1s"),
    ("blender", '{"exit": 1}'), ("mcp", '{"is_error": true}'), ("mcp", '{"isError":true}'),
    ("mcp", '{"ok":false}'), ("mcp", '{"truncated":')])
def test_tool_errors_are_not_success(tool, result):
    assert not result_ok(tool, result)


def test_file_write_is_checked_and_false_success_is_caught(client, tmp_path, monkeypatch):
    tools = client.app.state.orchestrator.operator.tools
    monkeypatch.setattr(tools, "_t_write_file", lambda **kw: "wrote it")
    t = start(client, op("write_file", path=str(tmp_path / "missing.txt"), content="hello"))
    _, a = pending(client, t["id"])
    client.post(f"/api/approvals/{a['id']}", json={"approve": True})
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "failed" and task["verified"] is False
    assert any(e["kind"] == "verification" and not e["ok"] for e in task["evidence"])


def test_cancel_completed_task_does_not_change_it(client):
    t = client.post("/api/tasks", json={"prompt": "hello", "kind": "operate"}).json()
    task = wait(client, t["id"], {"completed", "failed"})
    client.post(f"/api/tasks/{t['id']}/cancel")
    assert client.get(f"/api/tasks/{t['id']}").json()["status"] == task["status"]


def test_shutdown_does_not_hang_waiting_for_approval(app_factory, tmp_path):
    with app_factory() as client:
        t = start(client, op("write_file", path=str(tmp_path / "not-written.txt"), content="hi"))
        pending(client, t["id"])
    assert not (tmp_path / "not-written.txt").exists()


def test_copy_check_detects_wrong_content(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"abc")
    dst.write_bytes(b"xyz")
    ok, _ = check_file_result({"tool": "copy", "src": src, "dst": dst, "directory": False}, "copied")
    assert not ok


def test_readiness_does_not_call_models_or_expose_keys(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-appear")
    response = client.get("/api/diagnostics")
    assert response.status_code == 200
    assert "secret-must-not-appear" not in response.text
    assert response.json()["active_tasks"] == 0
    assert any(c["state"] == "limited" for c in response.json()["checks"])
    phone = {"host": "pc.ts.net", "x-forwarded-for": "100.64.0.9"}
    assert client.get("/api/diagnostics", headers=phone).status_code == 401
    client.cookies.set("hassan_key", client.app.state.access_key)
    assert client.get("/api/diagnostics", headers=phone).status_code == 200
    client.cookies.clear()


def test_cli_refuses_stale_process_identity(tmp_path, monkeypatch):
    from hassan_ai.cli import stop
    from .conftest import make_settings
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir()
    (settings.data_dir / "server.pid").write_text(str(os.getpid()))
    (settings.data_dir / "server.identity.json").write_text(json.dumps({"pid": os.getpid(), "created": 1}))
    assert stop(settings) is False
    assert (settings.data_dir / "server.pid").exists()


def test_unicode_write_has_independent_evidence(client, tmp_path):
    path = tmp_path / "arabic.txt"
    content = "مرحبا\nمن التلفون"
    t = start(client, op("write_file", path=str(path), content=content))
    _, a = pending(client, t["id"])
    client.post(f"/api/approvals/{a['id']}", json={"approve": True})
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "completed" and task["verified"] is True
    assert path.read_bytes() == content.encode("utf-8")


def test_stop_cancels_an_approved_running_command(client, tmp_path):
    import shlex
    ready = tmp_path / "running.pid"
    script = "import os,pathlib,time;pathlib.Path(" + repr(str(ready)) + ").write_text(str(os.getpid()));time.sleep(40)"
    def quote(value):
        return "'" + value.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(value)
    command = ("& " if os.name == "nt" else "") + quote(sys.executable) + " -c " + quote(script)
    t = start(client, op("run", command=command, cwd=str(tmp_path)))
    _, a = pending(client, t["id"])
    client.post(f"/api/approvals/{a['id']}", json={"approve": True})
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(.025)
        assert ready.exists()
        pid = int(ready.read_text())
    finally:
        client.post(f"/api/tasks/{t['id']}/cancel")
    task = wait(client, t["id"], {"cancelled", "failed"})
    assert task["status"] == "cancelled"
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
