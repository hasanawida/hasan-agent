"""Phone bridge boundary tests: fake companions only, no phone or OS input."""
import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from hassan_ai.phone import MAX_FRAME_BYTES, PHONE_PUBLIC_PATHS, PhoneHub, validate_action

LOCAL = {"origin": "http://testserver"}
REMOTE = {"host": "pc.ts.net", "origin": "https://pc.ts.net", "x-forwarded-for": "100.64.0.7",
          "x-forwarded-proto": "https"}
META = {"control_enabled": True, "screen_enabled": True, "width": 1080, "height": 2400}
JPEG = b"\xff\xd8\xff\xe0fake\xff\xd9"


@pytest.fixture
def phoneclient(client):
    client.app.state.phones.poll_seconds = .005
    return client


def pair(client, label="My Android"):
    code = client.post("/api/phones/pair-code", headers=LOCAL).json()["code"]
    reply = client.post("/api/phone/enroll", headers=REMOTE, json={"code": code, "label": label})
    assert reply.status_code == 200, reply.text
    value = reply.json()
    return value["device_id"], value["token"]


def auth(token):
    return {**REMOTE, "authorization": f"Bearer {token}"}


def poll(client, token, **changes):
    return client.post("/api/phone/poll", headers=auth(token), json={**META, **changes})


async def active_hub(tmp_path):
    hub = PhoneHub(tmp_path)
    hub.poll_seconds = .001
    hub.command_seconds = .05
    paired = await hub.enroll(hub.create_pair_code()["code"], "Test phone")
    ident = paired["device_id"]
    await hub.poll(ident, META)
    return hub, ident, paired["token"]


def test_pairing_requires_local_origin_but_enrollment_needs_only_code(phoneclient):
    c = phoneclient
    assert c.post("/api/phones/pair-code").status_code == 403
    assert c.post("/api/phones/pair-code", headers={"origin": "http://testserver:9999"}).status_code == 403
    assert c.post("/api/phones/pair-code", headers=auth(c.app.state.access_key)).status_code == 403
    response = c.post("/api/phones/pair-code", headers=LOCAL).json()
    assert len(response["code"]) == 6 and response["code"].isdigit()
    assert response["expires_in"] == 300
    assert "server_url" in response
    body = {"code": response["code"], "label": "Samsung"}
    unencrypted = {**REMOTE, "x-forwarded-proto": "http", "origin": "http://pc.ts.net"}
    assert c.post("/api/phone/enroll", headers=unencrypted, json=body).status_code == 403
    enrolled = c.post("/api/phone/enroll", headers=REMOTE, json=body)
    assert enrolled.status_code == 200
    assert c.post("/api/phone/enroll", headers=REMOTE, json=body).status_code == 403
    assert c.get("/api/phones").json()["devices"][0]["online"] is False


def test_pair_code_locks_after_five_guesses_and_expires(phoneclient):
    c = phoneclient
    code = c.post("/api/phones/pair-code", headers=LOCAL).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert c.post("/api/phone/enroll", headers=REMOTE, json={"code": wrong, "label": "Phone"}).status_code == 403
    assert c.post("/api/phone/enroll", headers=REMOTE, json={"code": code, "label": "Phone"}).status_code == 403
    code = c.post("/api/phones/pair-code", headers=LOCAL).json()["code"]
    c.app.state.phones.pair_until = time.monotonic() - 1
    assert c.post("/api/phone/enroll", headers=REMOTE, json={"code": code, "label": "Phone"}).status_code == 403


def test_phone_token_has_no_dashboard_scope_and_dashboard_key_has_no_phone_scope(phoneclient):
    c = phoneclient
    _, token = pair(c)
    assert poll(c, token).status_code == 200
    assert c.get("/api/phones", headers=auth(token)).status_code == 401
    assert c.get("/api/tasks", headers=auth(token)).status_code == 401
    assert poll(c, c.app.state.access_key).status_code == 401
    assert c.post("/api/phone/poll", headers=REMOTE, cookies={"hassan_key": token}, json=META).status_code == 401
    assert poll(c, "x" * 43).status_code == 401
    assert PHONE_PUBLIC_PATHS == {"/api/phone/enroll", "/api/phone/poll", "/api/phone/result", "/api/phone/frame"}


def test_registry_stores_only_hashed_identity_not_permission_or_screen(phoneclient):
    c = phoneclient
    ident, token = pair(c, "هاتفي")
    poll(c, token).raise_for_status()
    c.post("/api/phone/frame", headers={**auth(token), "content-type": "image/jpeg"}, content=JPEG).raise_for_status()
    hub = c.app.state.phones
    raw = hub.path.read_text(encoding="utf-8")
    rows = json.loads(raw)["devices"]
    assert token not in raw and "control_enabled" not in raw and "frame" not in raw
    assert set(rows[0]) == {"device_id", "label", "token_hash"}
    restarted = PhoneHub(hub.path.parent)
    assert restarted.authenticate(token) == ident
    state = restarted.list_devices()[0]
    assert not state["online"] and not state["control_enabled"] and not state["screen_enabled"]
    assert not state["busy"] and not state["frame_available"]


@pytest.mark.parametrize("metadata", [{**META, "control_enabled": "true"}, {**META, "width": -1},
                                      {**META, "height": 99999}, {**META, "extra": 1}])
def test_poll_rejects_invalid_metadata(phoneclient, metadata):
    _, token = pair(phoneclient)
    assert phoneclient.post("/api/phone/poll", headers=auth(token), json=metadata).status_code == 422


def test_local_grant_and_controller_ownership_are_required(phoneclient):
    c = phoneclient
    ident, token = pair(c)
    url = f"/api/phones/{ident}/session"
    assert c.post(url, headers=LOCAL, json={}).status_code == 409
    poll(c, token, control_enabled=False).raise_for_status()
    assert c.post(url, headers=LOCAL, json={}).status_code == 403
    poll(c, token).raise_for_status()
    assert c.post(url, json={}).status_code == 403
    owner = c.post(url, headers=LOCAL, json={}).json()["owner"]
    assert c.post(url, headers=LOCAL, json={}).status_code == 409
    assert c.post(url, headers=LOCAL, json={"owner": "wrong"}).status_code == 403
    assert c.post(url, headers=LOCAL, json={"owner": owner}).json()["owner"] == owner
    assert c.post(url + "/stop", headers=LOCAL, json={"owner": "wrong"}).status_code == 403
    assert c.post(url + "/stop", headers=LOCAL, json={"owner": owner}).status_code == 200
    assert not c.get("/api/phones").json()["devices"][0]["busy"]


def test_frame_bounds_permission_and_no_cache(phoneclient):
    c = phoneclient
    ident, token = pair(c)
    headers = {**auth(token), "content-type": "image/jpeg"}
    assert c.post("/api/phone/frame", headers=headers, content=JPEG).status_code == 403
    poll(c, token).raise_for_status()
    assert c.post("/api/phone/frame", headers=headers, content=b"not jpeg").status_code == 415
    assert c.post("/api/phone/frame", headers=headers, content=b"x" * (MAX_FRAME_BYTES + 1)).status_code == 413
    assert c.post("/api/phone/frame", headers=headers, content=JPEG).status_code == 200
    response = c.get(f"/api/phones/{ident}/frame")
    assert response.content == JPEG and "no-store" in response.headers["cache-control"]
    assert response.headers["content-type"] == "image/jpeg"
    poll(c, token, screen_enabled=False).raise_for_status()
    assert c.get(f"/api/phones/{ident}/frame").status_code == 404


def test_streamed_json_is_bounded_before_decoding(phoneclient):
    c = phoneclient
    _, token = pair(c)
    headers = {**auth(token), "content-type": "application/json"}
    def chunks():
        yield b'{"id":"'
        yield b"a" * 65536
    assert c.post("/api/phone/result", headers=headers, content=chunks()).status_code == 413
    assert c.post("/api/phone/poll", headers=headers, content=b"x" * 4097).status_code == 413
    assert c.post("/api/phone/poll", headers=headers, content=b"not JSON").status_code == 422


@pytest.mark.parametrize("action,args", [
    ("shell", {"command": "whoami"}), ("open_url", {"url": "https://example.com"}),
    ("install", {}), ("tap", {"x": True, "y": .5}), ("tap", {"x": float("nan"), "y": .5}),
    ("tap", {"x": 1.1, "y": 0}), ("tap", {"x": 10 ** 400, "y": 0}), ("swipe", {"x1": 0, "y1": 0, "x2": 1}),
    ("swipe", {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "duration_ms": 9999}),
    ("key", {"key": "power"}), ("text", {"text": "x" * 1001}), ("inspect", {"password": True})])
def test_only_bounded_phone_actions_are_accepted(action, args):
    with pytest.raises(HTTPException) as error:
        validate_action(action, args)
    assert error.value.status_code == 422


def test_command_delivery_result_and_no_replay(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "text", {"text": "مرحبا"}))
        await asyncio.sleep(0)
        delivered = (await hub.poll(ident, META))["command"]
        assert delivered["action"] == "text" and delivered["args"] == {"text": "مرحبا"}
        assert delivered["expires_at"] > int(time.time() * 1000)
        assert (await hub.poll(ident, META))["command"] is None
        body = {"id": delivered["id"], "ok": True, "result": {"changed": True}}
        await hub.result(ident, body)
        assert await task == body
        with pytest.raises(HTTPException) as late:
            await hub.result(ident, body)
        assert late.value.status_code == 409
        await hub.close()
    asyncio.run(scenario())


def test_timeout_and_cancellation_do_not_deliver_stale_command(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        hub.command_seconds = .01
        await hub.claim(ident, "agent:task", "agent")
        with pytest.raises(HTTPException) as expired:
            await hub.command(ident, "agent:task", "tap", {"x": .5, "y": .5})
        assert expired.value.status_code == 504
        assert (await hub.poll(ident, META))["command"] is None
        task = asyncio.create_task(hub.command(ident, "agent:task", "inspect", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert hub.devices[ident].pending is None
        assert (await hub.poll(ident, META))["command"] is None
        await hub.close()
    asyncio.run(scenario())


def test_phone_disable_fails_pending_and_releases_owner(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "inspect", {}))
        await asyncio.sleep(0)
        delivered = (await hub.poll(ident, META))["command"]
        assert delivered
        await hub.poll(ident, {**META, "control_enabled": False, "screen_enabled": False})
        with pytest.raises(HTTPException):
            await task
        assert not hub.list_devices()[0]["busy"]
        with pytest.raises(HTTPException) as denied:
            await hub.claim(ident, "agent:other", "agent")
        assert denied.value.status_code == 403
        await hub.close()
    asyncio.run(scenario())


def test_ownership_is_shared_across_manual_and_agent_and_expires(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.claim(ident, "manual:one")
        with pytest.raises(HTTPException) as conflict:
            await hub.claim(ident, "task:two", "agent")
        assert conflict.value.status_code == 409
        hub.devices[ident].lease_until = time.monotonic() - 1
        await hub.claim(ident, "task:two", "agent")
        assert hub.list_devices()[0]["owner_kind"] == "agent"
        with pytest.raises(HTTPException):
            await hub.release(ident, "manual:one")
        hub.devices[ident].last_seen = time.monotonic() - 46
        state = hub.list_devices()[0]
        assert not state["online"] and not state["busy"] and not state["control_enabled"]
        await hub.close()
    asyncio.run(scenario())


def test_result_from_another_device_cannot_complete_command(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        second = await hub.enroll(hub.create_pair_code()["code"], "Other")
        await hub.poll(second["device_id"], META)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "inspect", {}))
        await asyncio.sleep(0)
        command = (await hub.poll(ident, META))["command"]
        result = {"id": command["id"], "ok": True, "result": {"text": "visible label"}}
        with pytest.raises(HTTPException):
            await hub.result(second["device_id"], result)
        assert not task.done()
        await hub.result(ident, result)
        await task
        await hub.close()
    asyncio.run(scenario())


def test_revocation_fails_pending_clears_frame_and_invalidates_credential(tmp_path):
    async def scenario():
        hub, ident, token = await active_hub(tmp_path)
        await hub.frame(ident, JPEG)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "inspect", {}))
        await asyncio.sleep(0)
        await hub.revoke(ident)
        with pytest.raises(HTTPException):
            await task
        assert hub.list_devices() == []
        with pytest.raises(HTTPException):
            hub.authenticate(token)
        restarted = PhoneHub(tmp_path)
        assert restarted.list_devices() == []
        await hub.close()
    asyncio.run(scenario())


def test_revoke_route_is_local_and_key_rotation_revokes_devices(phoneclient):
    c = phoneclient
    ident, token = pair(c)
    url = f"/api/phones/{ident}/revoke"
    assert c.post(url, headers=auth(c.app.state.access_key)).status_code == 403
    assert c.post(url).status_code == 403
    assert c.post(url, headers=LOCAL).status_code == 200
    assert poll(c, token).status_code == 401
    _, token = pair(c)
    c.post("/api/remote/rotate", headers=LOCAL).raise_for_status()
    assert poll(c, token).status_code == 401


def test_malformed_registry_fails_closed_without_overwriting(tmp_path):
    path = tmp_path / "phones.json"
    path.write_text('{"devices":[{"device_id":"anything","label":"bad","token":"plain"}]}')
    hub = PhoneHub(tmp_path)
    assert hub.list_devices() == [] and hub.storage_error
    with pytest.raises(HTTPException):
        hub.create_pair_code()
    assert '"token":"plain"' in path.read_text()


def test_manual_stop_screen_retains_control_and_renew_cannot_undo_stop(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.frame(ident, JPEG)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "stop_screen", {}))
        await asyncio.sleep(0)
        command = (await hub.poll(ident, META))["command"]
        await hub.result(ident, {"id": command["id"], "ok": True, "result": {}})
        await task
        state = hub.list_devices()[0]
        assert state["control_enabled"] and not state["screen_enabled"] and not state["frame_available"]
        await hub.release(ident, "manual:one")
        await hub.claim(ident, "task:one", "agent")
        await hub.renew(ident, "task:one", "agent")
        await hub.release(ident, "task:one")
        with pytest.raises(HTTPException):
            await hub.renew(ident, "task:one", "agent")
        assert not hub.list_devices()[0]["busy"]
        await hub.close()
    asyncio.run(scenario())


def test_phone_control_works_with_screen_sharing_off(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.poll(ident, {**META, "screen_enabled": False})
        await hub.claim(ident, "task:one", "agent")
        task = asyncio.create_task(hub.command(ident, "task:one", "inspect", {}))
        await asyncio.sleep(0)
        command = (await hub.poll(ident, {**META, "screen_enabled": False}))["command"]
        await hub.result(ident, {"id": command["id"], "ok": True, "result": {"nodes": []}})
        assert (await task)["ok"]
        await hub.close()
    asyncio.run(scenario())


def test_nonfinite_json_is_rejected(phoneclient):
    c = phoneclient
    _, token = pair(c)
    payload = '{"id":"cmd_' + 'a' * 24 + '","ok":true,"result":{"x":NaN}}'
    response = c.post("/api/phone/result", headers={**auth(token), "content-type": "application/json"}, content=payload)
    assert response.status_code == 422


def test_second_command_is_rejected_and_result_before_delivery_is_rejected(tmp_path):
    async def scenario():
        hub, ident, _ = await active_hub(tmp_path)
        await hub.claim(ident, "manual:one")
        task = asyncio.create_task(hub.command(ident, "manual:one", "key", {"key": "home"}))
        await asyncio.sleep(0)
        pending = hub.devices[ident].pending
        with pytest.raises(HTTPException):
            await hub.result(ident, {"id": pending.id, "ok": True, "result": {}})
        with pytest.raises(HTTPException) as busy:
            await hub.command(ident, "manual:one", "key", {"key": "back"})
        assert busy.value.status_code == 409
        delivered = (await hub.poll(ident, META))["command"]
        assert delivered["id"] == pending.id
        await hub.result(ident, {"id": pending.id, "ok": True, "result": {}})
        await task
        await hub.close()
    asyncio.run(scenario())


def test_closing_wakes_long_poll_and_keeps_only_device_pairing(tmp_path):
    async def scenario():
        hub, ident, token = await active_hub(tmp_path)
        hub.poll_seconds = 20
        poll_task = asyncio.create_task(hub.poll(ident, META))
        await asyncio.sleep(0)
        await hub.close()
        assert await asyncio.wait_for(poll_task, .1) == {"command": None}
        restarted = PhoneHub(tmp_path)
        assert restarted.authenticate(token) == ident
        assert not restarted.list_devices()[0]["online"]
        await restarted.close()
    asyncio.run(scenario())
