"""Telegram bot end to end against a fake Bot API: pairing, tasks, approval buttons, voice, memory, schedules."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from hassan_ai.server import create_app

from .conftest import make_settings


class FakeTelegram:
    def __init__(self):
        self.updates: list[dict] = []
        self.sent: list[dict] = []
        self.answers: list[dict] = []
        self.uid = 0

    def push(self, chat_id, text=None, voice=False, callback=None):
        self.uid += 1
        if callback:
            self.updates.append({"update_id": self.uid, "callback_query": {
                "id": f"cq{self.uid}", "data": callback, "message": {"chat": {"id": chat_id}, "message_id": 1}}})
        else:
            msg = {"chat": {"id": chat_id}, "message_id": self.uid}
            if voice:
                msg["video_note" if voice == "round" else "voice"] = {"file_id": "v1"}
            else:
                msg["text"] = text
            self.updates.append({"update_id": self.uid, "message": msg})

    def texts(self, chat_id):
        return [m["text"] for m in self.sent if m.get("chat_id") == chat_id and "text" in m]

    async def handler(self, request: httpx.Request):
        path = request.url.path
        if path.startswith("/file/"):
            return httpx.Response(200, content=b"OGG")
        method = path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}") if request.headers.get("content-type", "").startswith("application/json") else {}
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "hassan_test_bot"}})
        if method == "getUpdates":
            offset = body.get("offset", 0)
            pending = [u for u in self.updates if u["update_id"] >= offset]
            if not pending:
                await asyncio.sleep(0.05)
            return httpx.Response(200, json={"ok": True, "result": pending})
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        if method == "answerCallbackQuery":
            self.answers.append(body)
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/v1.ogg"}})
        return httpx.Response(200, json={"ok": True, "result": {}})


def until(fn, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not reached")


@pytest.fixture
def bot_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)
    fake = FakeTelegram()
    app = create_app(make_settings(tmp_path), telegram_transport=httpx.MockTransport(fake.handler),
                     telegram_api="https://tg.test")

    async def fake_transcriber(audio, name):
        assert audio == b"OGG"
        return "شو في على سطح المكتب"

    app.state.telegram.transcriber = fake_transcriber
    with TestClient(app) as client:
        yield client, fake, tmp_path


def test_telegram_full_flow(bot_env):
    client, fake, tmp = bot_env
    HASSAN, STRANGER = 111, 999
    assert client.post("/api/telegram/token", json={"token": "not a token"}).status_code == 400
    st = client.post("/api/telegram/token", json={"token": "123:ABC"}).json()
    assert st["configured"] and st["username"] == "hassan_test_bot" and st["running"]
    assert "TELEGRAM_BOT_TOKEN=123:ABC" in (tmp / "hassan.env").read_text()

    # strangers are refused once and never start tasks
    fake.push(STRANGER, "delete everything")
    until(lambda: fake.texts(STRANGER))
    assert "خاص" in fake.texts(STRANGER)[0]
    assert client.get("/api/tasks").json() == []

    # pairing with the code shown on the PC dashboard
    code = client.post("/api/telegram/pair-code").json()["code"]
    fake.push(HASSAN, "/pair 000000" if code != "000000" else "/pair 111111")
    fake.push(HASSAN, f"/pair {code}")
    until(lambda: any("تم ربط" in t for t in fake.texts(HASSAN)))
    assert client.get("/api/telegram").json()["paired_chats"] == [HASSAN]

    # a task from Telegram that needs approval: buttons arrive, pressing ✅ completes it
    (tmp / "a.txt").write_text("a")
    op = json.dumps({"tool": "copy", "args": {"src": str(tmp / "a.txt"), "dst": str(tmp / "b.txt")}})
    fake.push(HASSAN, f"انسخ الملف\nOP {op}")
    msg = until(lambda: next((m for m in fake.sent if m.get("chat_id") == HASSAN and m.get("reply_markup")), None))
    buttons = [b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    assert any(b.startswith("ok:") for b in buttons) and any(b.startswith("all:") for b in buttons)
    fake.push(STRANGER, callback=buttons[0])  # a stranger pressing the button does nothing
    until(lambda: any(a.get("text") == "غير مسموح" for a in fake.answers))
    assert not (tmp / "b.txt").exists()
    fake.push(HASSAN, callback=next(b for b in buttons if b.startswith("ok:")))
    until(lambda: any(t.startswith("✅ خلصت") for t in fake.texts(HASSAN)))
    assert (tmp / "b.txt").read_text() == "a"
    task = client.get("/api/tasks").json()[0]
    assert task["status"] == "completed"

    # voice note -> transcription -> task
    fake.push(HASSAN, voice=True)
    until(lambda: any("سمعتك: شو في على سطح المكتب" in t for t in fake.texts(HASSAN)))
    until(lambda: any(t["prompt"] == "شو في على سطح المكتب" for t in client.get("/api/tasks").json()))
    heard = sum("سمعتك" in t for t in fake.texts(HASSAN))
    fake.push(HASSAN, voice="round")  # a round video note is understood the same way
    until(lambda: sum("سمعتك" in t for t in fake.texts(HASSAN)) > heard)

    # memory and schedules from the chat
    fake.push(HASSAN, "/remember بفضّل التقارير قصيرة وبالعربي")
    until(lambda: any("حفظتها" in t for t in fake.texts(HASSAN)))
    assert "قصيرة" in client.get("/api/profile").json()["notes"][0]["content"]
    fake.push(HASSAN, "/schedule daily 08:00 ابعتلي ملخص الملفات الجديدة")
    until(lambda: any(t.startswith("⏰") for t in fake.texts(HASSAN)))
    (sched,) = client.get("/api/schedules").json()
    assert sched["spec"] == "daily 08:00" and sched["origin"] == f"telegram:{HASSAN}"

    # unpair: the chat is refused again
    client.post("/api/telegram/unpair", json={"chat_id": HASSAN})
    n = len(client.get("/api/tasks").json())
    fake.push(HASSAN, "hello again")
    until(lambda: any("خاص" in t for t in fake.texts(HASSAN)))
    assert len(client.get("/api/tasks").json()) == n


def test_telegram_settings_are_pc_only(bot_env):
    client, _, _ = bot_env
    phone = {"host": "pc.ts.net", "x-forwarded-for": "100.64.0.9"}
    client.cookies.set("hassan_key", client.app.state.access_key)
    for method, path in (("get", "/api/telegram"), ("post", "/api/telegram/pair-code")):
        assert getattr(client, method)(path, headers=phone).status_code == 403
    assert client.post("/api/telegram/token", json={"token": "1:x"}, headers={**phone, "origin": "https://pc.ts.net"}).status_code == 403
    client.cookies.clear()


def test_scheduler_runs_due_tasks_once(client):
    s = client.post("/api/schedules", json={"text": "every 20m راجع التنزيلات"}).json()
    assert s["spec"] == "every 1200s"
    assert client.post("/api/schedules", json={"text": "every 5m x"}).status_code == 400
    sched = client.app.state.scheduler
    started = client.portal.call(lambda: sched.run_due(at=s["next_run"] + 1))  # inside the app's event loop
    assert len(started) == 1
    assert client.portal.call(lambda: sched.run_due(at=s["next_run"] + 2)) == []  # moved on, no double run
    task = client.get(f"/api/tasks/{started[0]}").json()
    assert task["origin"] == f"schedule:{s['id']}" and task["kind"] == "operate"
    assert client.delete(f"/api/schedules/{s['id']}").json() == {"ok": True}


def test_operator_remembers_and_learns_skills(client, tmp_path):
    client.post("/api/profile", json={"content": "مشروع الأقصى بمجلد Documents/AlAqsa"})
    t = client.post("/api/tasks", json={"prompt": "وين مشروع الأقصى؟", "kind": "operate"}).json()
    from .conftest import wait
    task = wait(client, t["id"], {"completed", "failed"})
    assert "AlAqsa" in task["decision"]  # the profile reached the operator's context
    op = lambda tool, **a: "OP " + json.dumps({"tool": tool, "args": a})  # noqa: E731
    t = client.post("/api/tasks", json={"kind": "operate", "prompt": "\n".join([
        "x", op("save_skill", name="Organize Downloads", description="sort downloads by type", steps="1. list\n2. move"),
        op("remember", note="my password is 1234"), op("search_history", query="الأقصى")])}).json()
    task = wait(client, t["id"], {"completed", "failed"})
    skills = client.get("/api/profile").json()["skills"]
    assert skills == [{"name": "organize-downloads", "description": "sort downloads by type"}]
    assert all("1234" not in n["content"] for n in client.get("/api/profile").json()["notes"])


def test_telegram_pairing_is_forgiving(bot_env):
    client, fake, tmp = bot_env
    client.post("/api/telegram/token", json={"token": "123:ABC"})
    fake.push(5, "/pair")
    until(lambda: any("مثلاً" in t for t in fake.texts(5)))
    # Arabic-keyboard digits with spaces
    code = client.post("/api/telegram/pair-code").json()["code"]
    arabic = code.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    fake.push(5, f"/pair {arabic[:3]} {arabic[3:]}")
    until(lambda: any("تم ربط" in t for t in fake.texts(5)))
    # just the code, without /pair
    code = client.post("/api/telegram/pair-code").json()["code"]
    fake.push(6, code)
    until(lambda: any("تم ربط" in t for t in fake.texts(6)))
    assert client.get("/api/telegram").json()["paired_chats"] == [5, 6]


def test_messages_continue_one_chat_until_new(bot_env):
    client, fake, tmp = bot_env
    client.post("/api/telegram/token", json={"token": "123:ABC"})
    code = client.post("/api/telegram/pair-code").json()["code"]
    fake.push(7, f"/pair {code}")
    until(lambda: any("تم ربط" in t for t in fake.texts(7)))

    def done_tasks(n):
        tasks = client.get("/api/tasks").json()
        return len(tasks) >= n and all(t["status"] == "completed" for t in tasks) and tasks

    fake.push(7, "اسمي حسن وبحب بلندر")
    until(lambda: done_tasks(1))
    fake.push(7, "شو اسمي؟")
    tasks = until(lambda: done_tasks(2))
    assert tasks[0]["conversation"] == tasks[1]["conversation"]
    op = client.app.state.orchestrator.operator
    second = client.app.state.memory.get_task(tasks[0]["id"])
    chat = op._chat(second)
    assert "Hassan: اسمي حسن وبحب بلندر" in chat and "شو اسمي" not in chat

    fake.push(7, "/new")
    until(lambda: any("محادثة جديدة" in t for t in fake.texts(7)))
    fake.push(7, "مرحبا")
    tasks = until(lambda: done_tasks(3))
    assert tasks[0]["conversation"] != tasks[1]["conversation"]
    assert op._chat(client.app.state.memory.get_task(tasks[0]["id"])) == "(this is the first message)"
