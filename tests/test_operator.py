"""Operator mode: the agent works on the PC; changes wait for Hassan's approval."""

import json
import os
import time
from pathlib import Path

from .conftest import wait


def op(tool, **args):
    return "OP " + json.dumps({"tool": tool, "args": args})


def start(client, *lines):
    prompt = "رتّب الملفات\n" + "\n".join(lines)
    return client.post("/api/tasks", json={"prompt": prompt, "kind": "operate"}).json()


def pending(client, task_id):
    for _ in range(200):
        task = client.get(f"/api/tasks/{task_id}").json()
        p = [a for a in task["approvals"] if a["status"] == "pending"]
        if p:
            return task, p[0]
        time.sleep(0.05)
    raise AssertionError("no pending approval")


def test_looking_is_automatic(client, tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.pdf").write_text("pdf")
    (tmp_path / "notes.txt").write_text("hello operator")
    t = start(client, op("list_dir", path=str(tmp_path / "Downloads")), op("read_file", path=str(tmp_path / "notes.txt")),
              op("search_files", root=str(tmp_path), pattern="*.pdf"), op("make_dir", path=str(tmp_path / "PDFs")))
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "completed", task["error"]
    assert task["approvals"] == []
    results = {e["title"].split()[0]: e for e in task["evidence"]}
    assert "a.pdf" in results["list_dir"]["detail"]
    assert "hello operator" in results["read_file"]["detail"]
    assert "a.pdf" in results["search_files"]["detail"]
    assert (tmp_path / "PDFs").is_dir()
    assert task["decision"].startswith("[")  # mock operator's final answer


def test_move_waits_for_approval_then_runs(client, tmp_path):
    src = tmp_path / "report.pdf"
    src.write_text("x")
    t = start(client, op("move", src=str(src), dst=str(tmp_path / "PDFs" / "report.pdf")))
    task, approval = pending(client, t["id"])
    assert task["status"] == "awaiting_approval"
    assert approval["action"] == "pc.move" and "report.pdf" in approval["title"]
    assert src.exists()  # nothing happens before approval
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True})
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "completed", task["error"]
    assert not src.exists() and (tmp_path / "PDFs" / "report.pdf").exists()


def test_rejected_action_is_not_done(client, tmp_path):
    f = tmp_path / "keep.txt"
    f.write_text("keep me")
    t = start(client, op("delete", path=str(f)))
    _, approval = pending(client, t["id"])
    client.post(f"/api/approvals/{approval['id']}", json={"approve": False})
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "failed"
    assert f.read_text() == "keep me"
    assert any(not e["ok"] and e["title"].startswith("delete") for e in task["evidence"])


def test_delete_goes_to_recoverable_trash(client, tmp_path):
    f = tmp_path / "old.log"
    f.write_text("log")
    t = start(client, op("delete", path=str(f)))
    _, approval = pending(client, t["id"])
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True})
    wait(client, t["id"], {"completed", "failed"})
    assert not f.exists()
    trashed = list((tmp_path / "data" / "trash").rglob("old.log"))
    assert trashed and trashed[0].read_text() == "log"
    assert (trashed[0].parent / "ORIGINAL_PATH.txt").read_text() == str(f)


def test_run_command_shows_exact_command_and_runs_after_approval(client, tmp_path):
    t = start(client, op("run", command="echo hassan-was-here", cwd=str(tmp_path)))
    _, approval = pending(client, t["id"])
    assert "echo hassan-was-here" in approval["diff"]
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True})
    task = wait(client, t["id"], {"completed", "failed"})
    run = next(e for e in task["evidence"] if e["title"].startswith("run"))
    assert run["ok"] and "hassan-was-here" in run["detail"]


def test_safety_rails(client, tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("PRIVATE")
    (tmp_path / "data" / "access_key").parent.mkdir(exist_ok=True)
    t = start(client, op("read_file", path="/etc/passwd"), op("read_file", path=str(tmp_path / ".ssh" / "id_rsa")),
              op("list_dir", path=str(tmp_path / "data")), op("read_file", path=str(tmp_path / ".env")),
              op("format_disk", path="C:\\"))
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["approvals"] == []  # nothing even asked
    blocked = [e for e in task["evidence"] if not e["ok"]]
    assert len(blocked) == 5
    for out in task["outputs"]:
        assert "PRIVATE" not in out["content"]


def test_opening_a_program_needs_approval_but_a_folder_does_not(client, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("hassan_ai.pc_tools.subprocess.Popen", lambda argv, **kw: opened.append(argv))
    if os.name == "nt":
        monkeypatch.setattr("hassan_ai.pc_tools.os.startfile", lambda path: opened.append(path))
    (tmp_path / "setup.exe").write_text("MZ")
    t = start(client, op("open", target=str(tmp_path)), op("open", target=str(tmp_path / "setup.exe")))
    task, approval = pending(client, t["id"])
    assert approval["action"] == "pc.open"
    assert len(opened) == 1  # the folder opened without asking
    client.post(f"/api/approvals/{approval['id']}", json={"approve": False})
    wait(client, t["id"], {"completed", "failed"})
    assert len(opened) == 1


def test_stop_button_cancels_waiting_task(client, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    t = start(client, op("delete", path=str(f)))
    _, approval = pending(client, t["id"])
    assert client.post(f"/api/tasks/{t['id']}/cancel").json() == {"ok": True}
    task = wait(client, t["id"], {"completed", "failed"})
    assert f.exists()
    assert all(a["status"] == "rejected" for a in task["approvals"])
    assert task["status"] == "cancelled" and "أوقفت" in task["decision"]


def test_phone_can_approve_operator_actions(client, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("a")
    t = start(client, op("copy", src=str(src), dst=str(tmp_path / "b.txt")))
    _, approval = pending(client, t["id"])
    phone = {"host": "pc.ts.net", "x-forwarded-for": "100.64.0.9", "origin": "https://pc.ts.net", "x-forwarded-proto": "https"}
    assert client.post(f"/api/approvals/{approval['id']}", json={"approve": True}, headers=phone).status_code == 401
    client.cookies.set("hassan_key", client.app.state.access_key)
    assert client.post(f"/api/approvals/{approval['id']}", json={"approve": True}, headers=phone).status_code == 200
    client.cookies.clear()
    wait(client, t["id"], {"completed", "failed"})
    assert (tmp_path / "b.txt").read_text() == "a"


def test_same_request_is_not_asked_again_and_failing_loops_stop(client, tmp_path):
    missing = str(tmp_path / "nope.txt")
    same = op("copy", src=missing, dst=str(tmp_path / "b.txt"))
    t = start(client, same, same, same, same)
    task, approval = pending(client, t["id"])
    client.post(f"/api/approvals/{approval['id']}", json={"approve": True})
    task = wait(client, t["id"], {"completed", "failed"})
    assert len(task["approvals"]) == 1  # asked once, not four times
    assert len([e for e in task["evidence"] if e["title"].startswith("copy")]) == 2  # third try is stopped


def test_rejected_request_is_not_asked_again(client, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    same = op("copy", src=str(tmp_path / "a.txt"), dst=str(tmp_path / "b.txt"))
    t = start(client, same, same)
    task, approval = pending(client, t["id"])
    client.post(f"/api/approvals/{approval['id']}", json={"approve": False})
    task = wait(client, t["id"], {"completed", "failed"})
    assert len(task["approvals"]) == 1 and not (tmp_path / "b.txt").exists()
