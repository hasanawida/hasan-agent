"""Free brains by default; paid (subscription) brains only with Hassan's OK."""

import time

import pytest
from fastapi.testclient import TestClient

from hassan_ai.llm import LLMError, MockLLM
from hassan_ai.providers import Route, RouterLLM
from hassan_ai.server import create_app

from .conftest import make_settings, wait


class Labeled(MockLLM):
    def __init__(self, label, broken=False):
        self.label, self.broken, self.calls = label, broken, 0

    async def complete(self, model, system, user, *, json_mode=False):
        self.calls += 1
        if self.broken:
            raise LLMError(f"{self.label}: down")
        comp = await super().complete(model, system, user, json_mode=json_mode)
        comp.model = self.label
        return comp


def router(free_broken=False):
    paid, free = Labeled("paid"), Labeled("free", broken=free_broken)
    r = RouterLLM({"paid": paid, "free": free}, routes={}, default=Route("paid"), fallback=["free"],
                  tiers={"simple": ["free", "paid"], "medium": ["paid", "free"], "complex": None}, paid={"paid"})
    return r, paid, free


def app(tmp_path, llm):
    return TestClient(create_app(make_settings(tmp_path), llm=llm))


def pending(c, task_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = [a for a in c.get(f"/api/tasks/{task_id}").json()["approvals"] if a["status"] == "pending"]
        if p:
            return p[0]
        time.sleep(0.05)
    raise AssertionError("no approval requested")


def test_easy_task_stays_free_without_asking(tmp_path):
    r, paid, free = router()
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي التنزيلات", "kind": "operate"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["status"] == "completed" and task["approvals"] == []
    assert {o["model"] for o in task["outputs"]} == {"free"} and paid.calls == 0


def test_hard_task_asks_before_paid_and_uses_it_when_approved(tmp_path):
    r, paid, free = router()
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "برمجلي نظام حجوزات مع خطة", "mode": "fast"}).json()
        a = pending(c, t["id"])
        assert a["action"] == "brain.paid" and "💳" in a["title"]
        assert paid.calls == 0  # nothing paid before the OK
        c.post(f"/api/approvals/{a['id']}", json={"approve": True})
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["status"] == "completed" and task["paid_ok"] is True
    assert {o["model"] for o in task["outputs"]} == {"paid"}


def test_rejecting_paid_continues_on_free(tmp_path):
    r, paid, free = router()
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "برمجلي لعبة كاملة", "mode": "fast"}).json()
        c.post(f"/api/approvals/{pending(c, t['id'])['id']}", json={"approve": False})
        task = wait(c, t["id"], {"completed", "failed"})
        events = c.get(f"/api/tasks/{t['id']}/events").json()
    assert task["status"] == "completed" and paid.calls == 0
    assert {o["model"] for o in task["outputs"]} == {"free"}
    assert any("رفضت المدفوع" in e["message"] for e in events)
    assert len(task["approvals"]) == 1  # asked once per task, not every step


def test_when_free_brains_fail_it_asks_then_uses_paid(tmp_path):
    r, paid, free = router(free_broken=True)
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي التنزيلات", "kind": "operate"}).json()
        a = pending(c, t["id"])
        assert "فشلت" in a["title"]
        c.post(f"/api/approvals/{a['id']}", json={"approve": True})
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["status"] == "completed" and {o["model"] for o in task["outputs"] if o["ok"]} == {"paid"}


def test_strict_free_never_asks_and_never_pays(tmp_path):
    r, paid, free = router(free_broken=True)
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي التنزيلات", "kind": "operate", "budget": "free"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["approvals"] == [] and paid.calls == 0
    assert task["status"] == "failed"


def test_paid_budget_does_not_ask(tmp_path):
    r, paid, free = router()
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "برمجلي لعبة", "mode": "fast", "budget": "best"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["approvals"] == [] and {o["model"] for o in task["outputs"]} == {"paid"}


def test_default_config_marks_subscriptions_paid():
    from pathlib import Path
    r = RouterLLM.from_config(Path(__file__).resolve().parents[1] / "configs" / "providers.yaml", "http://x", "", 10)
    assert r.paid == {"claude", "chatgpt", "gemini-sub"}
