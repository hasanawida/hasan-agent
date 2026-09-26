"""Cost-aware routing: easy tasks go to free brains, hard ones to the strong ones, with escalation."""

import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hassan_ai.llm import Completion, LLMError, MockLLM
from hassan_ai.orchestrator import classify_heuristic
from hassan_ai.providers import RouterLLM
from hassan_ai.server import create_app

from .conftest import make_settings, wait


@pytest.mark.parametrize("prompt,kind,expected", [
    ("قديش المساحة الفاضية على الكمبيوتر؟", "operate", "simple"),
    ("افتحلي مجلد التنزيلات", "operate", "simple"),
    ("برمجلي لعبة كاملة بـUnity مع خطة", "project", "complex"),
    ("صمم مشهد باب الأسباط بـBlender واعمل رندر", "operate", "complex"),
    ("افتح المشروع وأصلح الخلل بالصوت", "operate", None),  # mixed signals -> ask the free triage brain
    ("x" * 600, "operate", "complex"),
    ("شو في بسطح المكتب", "project", "medium"),  # team tasks are never 'simple'
])
def test_heuristic(prompt, kind, expected):
    assert classify_heuristic(prompt, kind)[0] == expected


class TierLLM(MockLLM):
    """Mock router: records which tier each call used; free brains can be made to misbehave."""
    tiers = {"simple": ["free"], "medium": ["mid"], "complex": None}

    def __init__(self, broken_free=False, triage_level="medium"):
        self.calls: list[str] = []
        self.broken_free = broken_free
        self.triage_level = triage_level

    def tier_uses_list(self, tier):
        return bool(tier and self.tiers.get(tier))

    async def complete(self, model, system, user, *, json_mode=False):
        self.calls.append(model)
        if model.startswith("triage@"):
            return Completion(text=json.dumps({"level": self.triage_level}), model="free/triage", duration=0)
        if model.endswith("@simple") and self.broken_free:
            return Completion(text="sorry I can't JSON", model="free/weak", duration=0)
        comp = await super().complete(model, system, user, json_mode=json_mode)
        comp.model = "free/x" if model.endswith("@simple") else "mid/x" if model.endswith("@medium") else "best/" + model
        return comp


def client_with(tmp_path, llm):
    return TestClient(create_app(make_settings(tmp_path), llm=llm))


def test_simple_task_uses_free_brain_and_fast_team(tmp_path):
    llm = TierLLM()
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي مجلد التنزيلات", "kind": "operate"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
        assert task["tier"] == "simple"
        assert all(o["model"] == "free/x" for o in task["outputs"])
        t = c.post("/api/tasks", json={"prompt": "ترجم hello للعربي", "mode": "auto"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
        assert task["tier"] == "medium" and task["resolved_mode"] in ("auto", "fast")


def test_complex_task_uses_each_role_best_brain(tmp_path):
    llm = TierLLM()
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "برمجلي نظام حجوزات مع خطة", "mode": "auto"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
        assert task["tier"] == "complex"
        models = {o["agent"]: o["model"] for o in task["outputs"]}
        assert models["coder"] == "best/coder" and models["manager"] == "best/manager"


def test_unsure_tasks_ask_the_free_triage_brain(tmp_path):
    llm = TierLLM(triage_level="simple")
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "افتح المشروع وأصلح الخلل بالصوت", "kind": "operate"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
    assert llm.calls[0] == "triage@simple"
    assert task["tier"] == "simple"
    assert task["usage"]["calls"] >= 2  # triage is counted in the task's usage


def test_budget_choice_overrides_triage(tmp_path):
    llm = TierLLM()
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "برمجلي لعبة", "kind": "operate", "budget": "free"}).json()
        assert wait(c, t["id"], {"completed", "failed"})["tier"] == "simple"
        t = c.post("/api/tasks", json={"prompt": "افتح التنزيلات", "kind": "operate", "budget": "best"}).json()
        assert wait(c, t["id"], {"completed", "failed"})["tier"] == "complex"
    assert not any(m.startswith("triage") for m in llm.calls)


def test_operator_escalates_when_free_brain_misbehaves(tmp_path):
    llm = TierLLM(broken_free=True)
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي مجلد التنزيلات", "kind": "operate"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
        events = c.get(f"/api/tasks/{t['id']}/events").json()
    assert task["status"] == "completed"
    assert task["tier"] == "medium"
    assert any(e["kind"] == "tier" and "⬆️" in e["message"] for e in events)
    assert [m for m in llm.calls if m.startswith("operator@")][:2] == ["operator@simple", "operator@simple"]


def test_router_tier_chain(tmp_path, monkeypatch):
    cfg = tmp_path / "p.yaml"
    cfg.write_text(textwrap.dedent("""
        backends:
          best: {type: mock}
          free: {type: api, url: "http://free.test/v1", key_env: HASSAN_T_FREE, models: [m]}
          free2: {type: mock}
        default: best
        fallback: [best]
        tiers: {simple: [free, free2], complex: roles}
    """))
    router = RouterLLM.from_config(cfg, "http://x", "", 10)
    assert router.tier_uses_list("simple") and not router.tier_uses_list("complex")
    import asyncio
    monkeypatch.delenv("HASSAN_T_FREE", raising=False)
    comp = asyncio.run(router.complete("operator@simple", "ROLE: operator", "### TASK\nx\n### HISTORY\n(nothing yet)"))
    assert comp.model == "mock/operator"  # free (no key) skipped -> free2 (mock) answered
    bad = tmp_path / "bad.yaml"
    bad.write_text("backends: {a: {type: mock}}\ndefault: a\ntiers: {easy: [a]}\n")
    with pytest.raises(ValueError):
        RouterLLM.from_config(bad, "http://x", "", 10)


def test_default_config_tiers():
    router = RouterLLM.from_config(Path(__file__).resolve().parents[1] / "configs" / "providers.yaml", "http://x", "", 10)
    assert router.tiers["simple"][0] == "groq" and router.tiers["complex"] is None


def test_free_budget_is_a_hard_ceiling(tmp_path):
    llm = TierLLM(broken_free=True)
    with client_with(tmp_path, llm) as c:
        t = c.post("/api/tasks", json={"prompt": "افتحلي مجلد التنزيلات", "kind": "operate", "budget": "free"}).json()
        task = wait(c, t["id"], {"completed", "failed"})
        events = c.get(f"/api/tasks/{t['id']}/events").json()
    assert task["tier"] == "simple"
    assert all(m.endswith("@simple") for m in llm.calls if m.startswith("operator"))
    assert any("⛔" in e["message"] for e in events)
