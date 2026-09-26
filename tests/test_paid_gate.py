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


class Babbler(Labeled):
    """A free brain that answers, but never with usable JSON."""

    async def complete(self, model, system, user, *, json_mode=False):
        comp = await super().complete(model, system, user, json_mode=json_mode)
        comp.text = "sorry, I cannot access cameras"
        return comp


def test_bad_free_brain_hands_over_to_the_next_free_one_before_asking_paid(tmp_path):
    paid, bad, good = Labeled("paid"), Babbler("bad"), Labeled("good")
    r = RouterLLM({"paid": paid, "bad": bad, "good": good}, routes={}, default=Route("paid"), fallback=["bad", "good"],
                  tiers={"simple": ["bad", "good", "paid"], "medium": ["paid"], "complex": None}, paid={"paid"})
    with app(tmp_path, r) as c:
        t = c.post("/api/tasks", json={"prompt": "افتح الكاميرا وصوّرني", "kind": "operate"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
    assert task["status"] == "completed" and task["approvals"] == [] and paid.calls == 0
    assert task["tier"] == "simple" and task["outputs"][-1]["model"] == "good"


def test_paid_answer_is_remembered_for_the_whole_chat(tmp_path):
    r, paid, free = router(free_broken=True)
    with app(tmp_path, r) as c:
        t1 = c.post("/api/tasks", json={"prompt": "افتحلي التنزيلات", "kind": "operate", "conversation": "c1"}).json()
        a = pending(c, t1["id"])
        c.post(f"/api/approvals/{a['id']}", json={"approve": False})
        wait(c, t1["id"], {"completed", "failed"})
        t2 = c.post("/api/tasks", json={"prompt": "وشو في عالديسكتوب", "kind": "operate", "conversation": "c1"}).json()
        task = wait(c, t2["id"], {"completed", "failed"})
        assert task["approvals"] == [] and paid.calls == 0  # not asked again in the same chat
        t3 = c.post("/api/tasks", json={"prompt": "افتحلي التنزيلات", "kind": "operate", "conversation": "c2"}).json()
        a3 = pending(c, t3["id"])
        c.post(f"/api/approvals/{a3['id']}", json={"approve": False})
        wait(c, t3["id"], {"completed", "failed"})
    assert a3["action"] == "brain.paid"  # a new chat asks again
