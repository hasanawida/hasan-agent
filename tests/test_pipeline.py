import time

from hassan_ai.llm import LLMError, MockLLM

from .conftest import wait


def test_health_and_agents(client):
    h = client.get("/api/health").json()
    assert h["mode"] == "mock" and h["agents"] == 9
    names = {a["name"] for a in client.get("/api/agents").json()}
    assert {"manager", "analyst", "planner", "researcher", "coder", "reviewer", "judge", "decision"} <= names
    assert client.get("/").status_code == 200


def test_full_team_without_workspace(client):
    t = client.post("/api/tasks", json={"prompt": "خطط لتحسين نظام الصوت", "mode": "auto"}).json()
    task = wait(client, t["id"])
    assert task["status"] == "completed", task["error"]
    agents = [o["agent"] for o in task["outputs"]]
    # researcher and coder run in parallel, so only the set (and the ends) are fixed
    assert sorted(agents) == sorted(["manager", "analyst", "planner", "researcher", "coder", "reviewer", "judge", "decision"])
    assert agents[0] == "manager" and agents[-1] == "decision"
    assert task["decision"]
    kinds = {e["kind"] for e in client.get(f"/api/tasks/{t['id']}/events").json()}
    assert {"created", "route", "agent", "done"} <= kinds


def test_fast_mode_skips_review(client):
    t = client.post("/api/tasks", json={"prompt": "quick", "mode": "fast"}).json()
    task = wait(client, t["id"])
    agents = {o["agent"] for o in task["outputs"]}
    assert agents == {"manager", "planner", "coder", "decision"}


def test_consensus_runs_multiple_coders_and_cross_review(client):
    t = client.post("/api/tasks", json={"prompt": "critical change", "mode": "consensus"}).json()
    task = wait(client, t["id"])
    coders = [o for o in task["outputs"] if o["agent"] == "coder"]
    assert len(coders) == 3
    assert len({o["model"] for o in coders}) == 3
    assert any(o["agent"] == "cross_reviewer" for o in task["outputs"])


def test_execute_collects_evidence(client, py_project):
    t = client.post("/api/tasks", json={"prompt": "افحص المشروع", "workspace": str(py_project),
                                        "execute": True, "project": "demo"}).json()
    task = wait(client, t["id"])
    assert task["status"] == "completed", task["error"]
    kinds = {e["kind"]: e for e in task["evidence"]}
    assert {"inspect", "git_status", "git_diff", "test"} <= set(kinds)
    assert kinds["test"]["ok"], kinds["test"]["detail"]
    assert task["verified"] is True
    assert task["checkpoint"]["head"]
    assert client.get("/api/memory/demo").json()  # decision remembered


def test_workspace_outside_allowed_roots_rejected(client):
    r = client.post("/api/tasks", json={"prompt": "x", "workspace": "/"})
    assert r.status_code == 400


def test_approval_apply_verify_and_rollback(client, py_project):
    new = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    prompt = f"أضف دالة الطرح [[write calc.py]]{new}[[/write]]"
    t = client.post("/api/tasks", json={"prompt": prompt, "workspace": str(py_project), "execute": True}).json()
    task = wait(client, t["id"])
    assert task["status"] == "awaiting_approval"
    (approval,) = [a for a in task["approvals"] if a["status"] == "pending"]
    assert "+def sub(a, b):" in approval["diff"]
    assert "sub" not in (py_project / "calc.py").read_text()  # nothing written before approval

    assert client.post(f"/api/approvals/{approval['id']}", json={"approve": True}).status_code == 200
    assert client.post(f"/api/approvals/{approval['id']}", json={"approve": True}).status_code == 409
    task = wait(client, t["id"], {"completed", "failed"})
    assert task["status"] == "completed", task["error"]
    assert (py_project / "calc.py").read_text() == new
    assert task["verified"] is True
    assert any(e["kind"] == "write" for e in task["evidence"])

    restored = client.post(f"/api/tasks/{t['id']}/rollback").json()["restored"]
    assert restored == ["calc.py"]
    assert "sub" not in (py_project / "calc.py").read_text()


def test_rejected_changes_leave_files_untouched(client, py_project):
    before = (py_project / "calc.py").read_text()
    prompt = "[[write calc.py]]broken[[/write]]"
    t = client.post("/api/tasks", json={"prompt": prompt, "workspace": str(py_project), "execute": True}).json()
    task = wait(client, t["id"])
    aid = task["approvals"][0]["id"]
    client.post(f"/api/approvals/{aid}", json={"approve": False})
    task = wait(client, t["id"], {"rejected", "failed"})
    assert task["status"] == "rejected"
    assert (py_project / "calc.py").read_text() == before


def test_failed_verification_triggers_repair_round(client, py_project):
    prompt = "[[write calc.py]]def add(a, b):\n    return a - b\n[[/write]]"
    t = client.post("/api/tasks", json={"prompt": prompt, "workspace": str(py_project), "execute": True}).json()
    task = wait(client, t["id"])
    client.post(f"/api/approvals/{task['approvals'][0]['id']}", json={"approve": True})
    # After the failing test the Coder is asked to repair and a NEW approval is requested.
    for _ in range(600):
        task = client.get(f"/api/tasks/{t['id']}").json()
        if task["repair_round"] == 1 and task["status"] == "awaiting_approval":
            break
        time.sleep(0.1)
    assert task["repair_round"] == 1
    assert any(e["kind"] == "test" and not e["ok"] for e in task["evidence"])
    assert len(task["approvals"]) == 2


def test_secret_files_never_read(client, py_project):
    prompt = "read secrets"
    t = client.post("/api/tasks", json={"prompt": prompt, "workspace": str(py_project)}).json()
    task = wait(client, t["id"])
    for out in task["outputs"]:
        assert "SECRET=1" not in out["content"]


class FlakyLLM(MockLLM):
    async def complete(self, model, system, user, *, json_mode=False):
        if model == "coder":
            raise LLMError("coder: provider down")
        return await super().complete(model, system, user, json_mode=json_mode)


def test_model_fallback(app_factory):
    with app_factory(FlakyLLM()) as client:
        t = client.post("/api/tasks", json={"prompt": "x", "mode": "fast"}).json()
        task = wait(client, t["id"])
        assert task["status"] == "completed", task["error"]
        coder = [o for o in task["outputs"] if o["agent"] == "coder"][0]
        assert coder["model"] == "mock/coder-backup"
        stats = client.get("/api/models/stats").json()
        assert any(s["model"] == "coder" and s["success_rate"] == 0 for s in stats)
        events = client.get(f"/api/tasks/{t['id']}/events").json()
        assert any(e["kind"] == "fallback" for e in events)
