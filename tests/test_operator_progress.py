"""Ownership, durable checkpoints and explicit continuations use fake input/LLMs only."""
import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from hassan_ai.desktop import DesktopControl
from hassan_ai.llm import MockLLM
from hassan_ai.operator_mode import Operator
from hassan_ai.schemas import Mode, TaskRecord, TaskStatus
from .conftest import wait
from .test_desktop import FakeInput
from .test_operator import op, pending, start


class ResumeLLM(MockLLM):
    def __init__(self, actions):
        self.actions = actions
        self.operator_calls = 0
        self.contexts = []

    def _answer(self, role, model, task, user):
        if role != "operator":
            return super()._answer(role, model, task, user)
        self.contexts.append(user)
        self.operator_calls += 1
        if self.operator_calls == 1:
            return json.dumps({"actions": self.actions})
        return json.dumps({"done": True, "outcome": "completed", "answer": "Finished"})


def digest(tool, args):
    return hashlib.sha256(Operator.signature(tool, args).encode()).hexdigest()


def seed(orch, **fields):
    task = TaskRecord(prompt="Continue this goal", mode=Mode.auto, kind="operate", status=TaskStatus.incomplete,
                      budget="free", **fields)
    orch.memory.save_task(task)
    return task


def test_manual_takeover_waits_for_agent_cleanup_and_blocks_new_agents():
    async def scenario():
        desktop = DesktopControl()
        desktop.supported = True
        desktop.backend_factory = FakeInput
        await desktop.enable()
        await desktop.claim_agent("first")
        entered, finish = asyncio.Event(), asyncio.Event()
        async def stop(task_id):
            assert task_id == "first"
            entered.set()
            await finish.wait()
            await desktop.release_agent(task_id)
        desktop.on_takeover = stop
        socket = SimpleNamespace()
        connect = asyncio.create_task(desktop.connect(socket))
        await entered.wait()
        assert not connect.done() and desktop.status()["handoff_pending"]
        with pytest.raises(HTTPException):
            await desktop.claim_agent("second")
        finish.set()
        assert await connect is True
        assert desktop.status()["owner_kind"] == "manual"
        with pytest.raises(HTTPException):
            await desktop.claim_agent("first")
        await desktop.disconnect(socket)
        await desktop.close()
    asyncio.run(scenario())


def test_manual_takeover_cannot_override_agent_without_confirmed_cleanup():
    async def scenario():
        desktop = DesktopControl()
        desktop.supported = True
        desktop.backend_factory = FakeInput
        await desktop.enable()
        await desktop.claim_agent("first")
        desktop.on_takeover = AsyncMock()  # broken callback that leaves work alive
        assert await desktop.connect(object()) is False
        assert desktop.agent_owner == "first" and desktop.owner is None
        await desktop.release_agent("first")
        await desktop.close()
    asyncio.run(scenario())


def test_manual_owner_blocks_pc_mutation_without_executing(client, monkeypatch):
    orch = client.app.state.orchestrator
    desktop = client.app.state.desktop
    orch.operator.desktop = desktop
    desktop.owner = object()  # input backend is never called
    command = AsyncMock(return_value="should not run")
    original = orch.operator.tools.execute
    async def execute(tool, args):
        if tool == "make_dir":
            return await command(tool, args)
        return await original(tool, args)
    monkeypatch.setattr(orch.operator.tools, "execute", execute)
    task = start(client, op("make_dir", path=str(orch.settings.allowed_roots[0] / "never-made")))
    result = wait(client, task["id"], {"failed", "completed"})
    assert result["status"] == "failed"
    command.assert_not_awaited()
    desktop.owner = None


def test_intent_is_durable_before_effect_and_cancelled_worker_requires_resolution(client, tmp_path, monkeypatch):
    orch = client.app.state.orchestrator
    entered, finish = threading.Event(), threading.Event()
    original = orch.operator.tools._t_write_file
    def blocked_write(**args):
        entered.set()
        assert finish.wait(10)
        return original(**args)
    monkeypatch.setattr(orch.operator.tools, "_t_write_file", blocked_write)
    task = start(client, op("write_file", path=str(tmp_path / "intent.txt"), content="written"))
    _, approval = pending(client, task["id"])
    client.post('/api/approvals/' + approval['id'], json={"approve": True}).raise_for_status()
    try:
        assert entered.wait(5)
        saved = orch.memory.get_task(task["id"])
        assert saved.operator_pending["tool"] == "write_file" and saved.resume_blocked
        client.post('/api/tasks/' + task['id'] + '/cancel').raise_for_status()
    finally:
        finish.set()
    result = wait(client, task["id"], {"cancelled"})
    assert result["resume_blocked"] and result["operator_pending"]["mutation"]
    with pytest.raises(ValueError, match="uncertain"):
        client.portal.call(orch.resume, task["id"])


def test_resume_skips_acknowledged_mutation_and_observes_first(app_factory, tmp_path, monkeypatch):
    path = tmp_path / "created"
    path.mkdir()
    args = {"path": str(path)}
    brain = ResumeLLM([{"tool": "make_dir", "args": args}])
    with app_factory(llm=brain) as client:
        orch = client.app.state.orchestrator
        previous = seed(orch, operator_history=[{"tool": "make_dir", "args": args, "ok": True,
                          "acknowledged": True, "mutation": True, "result": "created"}],
                        operator_completed=[digest("make_dir", args)])
        make_dir = AsyncMock()
        original = orch.operator.tools.execute
        async def execute(tool, values):
            if tool == "make_dir":
                return await make_dir(tool, values)
            return await original(tool, values)
        monkeypatch.setattr(orch.operator.tools, "execute", execute)
        resumed = client.portal.call(orch.resume, previous.id)
        result = wait(client, resumed.id, {"completed", "failed"})
        assert result["status"] == "completed"
        assert result["resume_of"] == previous.id and result["resume_count"] == 1
        assert any(row.get("skipped") for row in result["operator_history"])
        assert "Fresh observation before continuing" in brain.contexts[0]
        make_dir.assert_not_awaited()
        assert client.portal.call(orch.resume, previous.id).id == resumed.id
        assert orch.memory.get_task(previous.id).resume_child == resumed.id


def test_resumed_new_mutation_requires_a_new_approval(app_factory, tmp_path):
    path = tmp_path / "new.txt"
    brain = ResumeLLM([{"tool": "write_file", "args": {"path": str(path), "content": "new"}}])
    with app_factory(llm=brain) as client:
        orch = client.app.state.orchestrator
        previous = seed(orch)
        orch.operator._trusted[previous.id] = {"write_file"}
        resumed = client.portal.call(orch.resume, previous.id)
        _, approval = pending(client, resumed.id)
        assert approval["task_id"] == resumed.id and not path.exists()
        client.post('/api/tasks/' + resumed.id + '/cancel').raise_for_status()
        wait(client, resumed.id, {"cancelled"})


def test_restart_exposes_uncertain_checkpoint_without_starting_work(app_factory, tmp_path):
    with app_factory() as client:
        orch = client.app.state.orchestrator
        args = {"path": str(tmp_path / "maybe.txt"), "content": "x"}
        task = seed(orch, operator_pending={"tool": "write_file", "args": args,
                    "signature": digest("write_file", args), "mutation": True})
        task.status = TaskStatus.running
        orch.memory.save_task(task)
    with app_factory() as client:
        orch = client.app.state.orchestrator
        saved = orch.memory.get_task(task.id)
        assert saved.status == TaskStatus.failed and saved.resume_blocked
        assert not orch._jobs
        with pytest.raises(ValueError):
            client.portal.call(orch.resume, task.id)
        child = client.portal.call(orch.resume, task.id, "completed")
        result = wait(client, child.id, {"completed", "failed"})
        assert digest("write_file", args) in result["operator_completed"]
        assert result["operator_pending"] is None
        assert not (tmp_path / "maybe.txt").exists()  # user resolution never executes it


def test_changed_file_is_not_silently_replayed_or_claimed_verified(app_factory, tmp_path):
    path = tmp_path / "changed.txt"
    path.write_text("changed by the user")
    args = {"path": str(path), "content": "old"}
    brain = ResumeLLM([{"tool": "write_file", "args": args}])
    with app_factory(llm=brain) as client:
        orch = client.app.state.orchestrator
        previous = seed(orch, operator_history=[{"tool": "write_file", "args": args, "ok": True,
                         "acknowledged": True, "mutation": True, "result": "written"}],
                        operator_completed=[digest("write_file", args)])
        child = client.portal.call(orch.resume, previous.id)
        result = wait(client, child.id, {"incomplete", "completed", "failed"})
        assert path.read_text() == "changed by the user"
        assert result["status"] == "incomplete" and result["verified"] is False


@pytest.mark.parametrize("visible_text, expected_state, verified", [("Saved", "completed", None),
                                                                      ("Error", "incomplete", False)])
def test_phone_changes_get_independent_readback_and_honest_criterion_result(app_factory, monkeypatch,
                                                                           visible_text, expected_state, verified):
    from .test_phone_operator import setup_phone, PHONE
    brain = ResumeLLM([{"tool": "phone_tap", "args": {"x": .5, "y": .5},
                       "expect": {"text_contains": "Saved"}}])
    with app_factory(llm=brain) as client:
        hub = setup_phone(client, monkeypatch)
        async def result(device_id, owner, action, args):
            return {"ok": True, "result": {"nodes": [{"text": visible_text}]} if action == "inspect" else {"executed": True}}
        hub.command.side_effect = result
        response = client.post('/api/tasks', json={"prompt": "Save on my phone", "kind": "operate", "device_id": PHONE})
        tid = response.json()["id"]
        _, approval = pending(client, tid)
        client.post('/api/approvals/' + approval['id'], json={"approve": True}).raise_for_status()
        task = wait(client, tid, {"completed", "failed", "incomplete"})
        assert task["status"] == expected_state
        assert task["verified"] is verified  # even matched literal text is not proof of the whole user goal
        assert [call.args[2] for call in hub.command.await_args_list] == ["tap", "inspect", "inspect"]
        checks = [row for row in task["evidence"] if row["kind"] == "ui_check"]
        assert len(checks) == 1 and checks[0]["ok"] == (visible_text == "Saved")
        assert len([row for row in task["evidence"] if row["kind"] == "observation"]) == 2


def test_failed_final_phone_read_does_not_claim_completion(app_factory, monkeypatch):
    from .test_phone_operator import setup_phone, PHONE
    brain = ResumeLLM([{"tool": "phone_key", "args": {"key": "home"}}])
    with app_factory(llm=brain) as client:
        hub = setup_phone(client, monkeypatch)
        hub.command.side_effect = [{"ok": True, "result": {}},
                                   {"ok": True, "result": {"nodes": []}},
                                   {"ok": False, "result": {}, "error": "Phone became locked"}]
        tid = client.post('/api/tasks', json={"prompt": "Home", "kind": "operate", "device_id": PHONE}).json()["id"]
        _, approval = pending(client, tid)
        client.post('/api/approvals/' + approval['id'], json={"approve": True}).raise_for_status()
        task = wait(client, tid, {"completed", "failed", "incomplete"})
        assert task["status"] == "incomplete" and task["verified"] is None
        assert "غير مؤكدة" in task["verification_summary"]
        assert task["operator_pending"] is None


def test_browser_session_revocation_is_checked_on_each_desktop_message(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    from .test_desktop import enable, PHONE as REMOTE_PHONE
    controller, fake = enable(client)
    allowed = {"value": True, "calls": 0}
    def authenticate(socket):
        allowed["calls"] += 1
        return allowed["value"]
    monkeypatch.setattr(client.app.state, 'authenticate_browser', authenticate, raising=False)
    with client.websocket_connect('/api/desktop/control', headers=REMOTE_PHONE) as socket:
        assert socket.receive_json() == {"type": "ready"}
        allowed["value"] = False
        socket.send_json({"action": "key", "key": "x", "down": True})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert allowed["calls"] >= 2 and fake.events == [] and controller.owner is None


def test_resolve_not_completed_allows_new_approval_but_never_reuses_old_trust(app_factory, tmp_path):
    args = {"path": str(tmp_path / "resolved.txt"), "content": "new"}
    brain = ResumeLLM([{"tool": "write_file", "args": args}])
    with app_factory(llm=brain) as client:
        orch = client.app.state.orchestrator
        previous = seed(orch, operator_pending={"tool": "write_file", "args": args,
                        "signature": digest("write_file", args), "mutation": True}, resume_blocked=True)
        child = client.portal.call(orch.resume, previous.id, "not_completed")
        _, approval = pending(client, child.id)
        assert not (tmp_path / "resolved.txt").exists()
        client.post('/api/approvals/' + approval['id'], json={"approve": True}).raise_for_status()
        task = wait(client, child.id, {"completed", "failed"})
        assert task["status"] == "completed"
        assert (tmp_path / "resolved.txt").read_text() == "new"


def test_a_later_blocked_read_cannot_clear_an_uncertain_mutation(client, tmp_path, monkeypatch):
    orch = client.app.state.orchestrator
    path = tmp_path / "partially-written.txt"
    def write_then_fail(**args):
        path.write_text(args["content"])
        raise RuntimeError("Lost confirmation after the effect")
    monkeypatch.setattr(orch.operator.tools, '_t_write_file', write_then_fail)
    task = start(client, op("write_file", path=str(path), content="may have happened"), op("system_info"))
    _, approval = pending(client, task["id"])
    client.post('/api/approvals/' + approval['id'], json={"approve": True}).raise_for_status()
    result = wait(client, task["id"], {"completed", "failed", "incomplete"})
    assert path.exists()
    assert result["resume_blocked"]
    assert result["operator_pending"]["tool"] == "write_file"
    with pytest.raises(ValueError, match="uncertain"):
        client.portal.call(orch.resume, task["id"])
