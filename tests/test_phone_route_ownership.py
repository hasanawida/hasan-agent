"""Browser routes cannot impersonate an agent's predictable task owner."""
LOCAL = {"origin": "http://testserver"}
META = {"control_enabled": True, "screen_enabled": False, "width": 1080, "height": 2400}


def test_manual_command_and_stop_cannot_reuse_agent_task_owner(client):
    hub = client.app.state.phones
    hub.poll_seconds = .001
    code = client.post("/api/phones/pair-code", headers=LOCAL).json()["code"]
    enrolled = client.post("/api/phone/enroll", json={"code": code, "label": "Fake Android"}).json()
    ident, token = enrolled["device_id"], enrolled["token"]
    client.post("/api/phone/poll", headers={"authorization": "Bearer " + token}, json=META).raise_for_status()
    owner = "task:visible-task-id"
    client.portal.call(hub.claim, ident, owner, "agent")
    # Knowing a public task ID is insufficient to inject input or stop its lease.
    response = client.post(f"/api/phones/{ident}/command", headers=LOCAL,
                           json={"owner": owner, "action": "tap", "args": {"x": .5, "y": .5}})
    assert response.status_code == 403
    assert hub.devices[ident].pending is None and hub.devices[ident].queue.empty()
    assert client.post(f"/api/phones/{ident}/session/stop", headers=LOCAL, json={"owner": owner}).status_code == 403
    assert hub.devices[ident].owner == owner and hub.devices[ident].owner_kind == "agent"
    # The agent's direct API remains usable and can end its own lease.
    client.portal.call(hub.renew, ident, owner, "agent")
    client.portal.call(hub.release, ident, owner)
    manual = client.post(f"/api/phones/{ident}/session", headers=LOCAL, json={}).json()["owner"]
    assert client.post(f"/api/phones/{ident}/session/stop", headers=LOCAL, json={"owner": manual}).status_code == 200
