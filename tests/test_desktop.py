"""Desktop authorization and lifecycle; all OS input is replaced by fakes."""
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from hassan_ai.desktop import InputState, valid_input

LOCAL = {"origin": "http://testserver"}
PHONE = {"host": "pc.ts.net", "origin": "https://pc.ts.net", "x-forwarded-for": "100.64.0.7",
         "x-forwarded-proto": "https"}


class FakeInput:
    def __init__(self):
        self.events = []
        self.releases = 0
    def apply(self, data):
        self.events.append(data)
    def release(self):
        self.releases += 1


def enable(client):
    controller = client.app.state.desktop
    fake = FakeInput()
    controller.supported = True
    controller.backend_factory = lambda: fake
    response = client.post("/api/desktop/enable", headers=LOCAL)
    assert response.status_code == 200, response.text
    return controller, fake


def flush(socket):
    socket.send_json({"action": "heartbeat"})
    assert socket.receive_json() == {"type": "pong"}


def test_desktop_is_off_by_default_and_needs_local_enable(client):
    assert client.get("/api/desktop").json()["enabled"] is False
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        assert socket.receive_json()["type"] == "error"
    key = client.app.state.access_key
    phone = {**PHONE, "authorization": f"Bearer {key}"}
    assert client.get("/api/desktop", headers=PHONE).status_code == 401
    assert client.post("/api/desktop/enable", headers=phone).status_code == 403
    assert client.post("/api/desktop/enable").status_code == 403  # no Origin
    assert client.post("/api/desktop/enable", headers={"origin": "http://testserver:9999"}).status_code == 403
    assert client.get("/api/desktop", headers=phone).json()["local"] is False


def test_unsupported_host_does_not_enable(client):
    client.app.state.desktop.supported = False
    assert client.post("/api/desktop/enable", headers=LOCAL).status_code == 503
    assert client.get("/api/desktop").json()["enabled"] is False


@pytest.mark.parametrize("headers", [{}, {"origin": "https://evil.example"},
    {"origin": "http://testserver:9999"}, {"origin": "http://["}, PHONE,
    {"host": "127.0.0.1", "origin": "http://127.0.0.1", "x-forwarded-for": "100.64.0.7"}])
def test_websocket_checks_origin_and_auth_separately_from_http(client, headers):
    _, fake = enable(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/desktop/control", headers=headers):
            pytest.fail("Unauthenticated or cross-site socket was accepted")
    assert fake.events == []


def test_authenticated_phone_controls_but_plain_http_is_rejected(client):
    _, fake = enable(client)
    auth = {"authorization": f"Bearer {client.app.state.access_key}"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/desktop/control", headers={**PHONE, **auth,
                                      "origin": "http://pc.ts.net", "x-forwarded-proto": "http"}):
            pytest.fail("Remote unencrypted socket was accepted")
    with client.websocket_connect("/api/desktop/control", headers={**PHONE, **auth}) as socket:
        assert socket.receive_json() == {"type": "ready"}
        for event in [{"action": "move", "x": .2, "y": .7},
                      {"action": "key", "key": "Control", "down": True},
                      {"action": "text", "text": "مرحبا من الهاتف"}, {"action": "release"}]:
            socket.send_json(event)
        flush(socket)
        assert len(fake.events) == 4
        assert fake.events[2]["text"] == "مرحبا من الهاتف"


def test_one_controller_at_a_time_and_disconnect_releases(client):
    service, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect("/api/desktop/control", headers=LOCAL) as second:
            assert second.receive_json()["type"] == "error"
        first.send_json({"action": "button", "button": "left", "down": True})
        flush(first)
        assert client.get("/api/desktop").json()["connected"]
    deadline = time.monotonic() + 2
    while (service.owner is not None or not fake.releases) and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.owner is None and fake.releases >= 1
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as replacement:
        assert replacement.receive_json()["type"] == "ready"


@pytest.mark.parametrize("event", [{"action": "move", "x": -1, "y": 0},
    {"action": "key", "key": "shell", "down": True}, {"action": "text", "text": "x" * 257},
    {"action": "button", "button": "left", "down": "yes"}, [1, 2]])
def test_invalid_messages_close_without_input(client, event):
    _, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        socket.send_json(event)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert fake.events == []


def test_binary_input_is_rejected(client):
    _, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        socket.send_bytes(b"not input")
        with pytest.raises(WebSocketDisconnect): socket.receive_json()
    assert not fake.events


@pytest.mark.parametrize("path", ["/api/desktop/disable", "/api/remote/rotate"])
def test_revocation_and_key_rotation_close_active_control(client, path):
    _, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        socket.send_json({"action": "key", "key": "a", "down": True})
        flush(socket)
        assert client.post(path, headers=LOCAL).status_code == 200
        with pytest.raises(WebSocketDisconnect): socket.receive_json()
    assert fake.releases >= 1
    assert not client.get("/api/desktop").json()["enabled"]


@pytest.mark.parametrize("expire_grant", [False, True])
def test_silent_connection_and_grant_expiry_release_input(client, expire_grant):
    service, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        if expire_grant:
            service.expires = time.monotonic() - 1
        else:
            service.last_seen = time.monotonic() - 20
        with pytest.raises(WebSocketDisconnect): socket.receive_json()
    assert fake.releases >= 1


def test_stalled_keys_are_released_while_connection_is_alive(client):
    service, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        service.last_input = time.monotonic() - 10
        deadline = time.monotonic() + 2
        while not fake.releases and time.monotonic() < deadline:
            time.sleep(.01)
        assert fake.releases >= 1
        flush(socket)


class Device:
    def __init__(self): self.pressed, self.released, self.typed = [], [], []
    def press(self, key): self.pressed.append(key)
    def release(self, key): self.released.append(key)
    def type(self, value): self.typed.append(value)
    def scroll(self, x, y): self.scrolled = (x, y)


def test_virtual_desktop_coordinates_modifiers_text_and_release():
    mouse, keyboard = Device(), Device()
    backend = InputState(mouse, keyboard, {"left": "L"}, {"Control": "CTRL"}, lambda: (-1920, -100, 3840, 1080))
    backend.apply({"action": "move", "x": 1, "y": 0})
    assert mouse.position == (1919, -100)
    backend.apply({"action": "button", "button": "left", "down": True})
    backend.apply({"action": "key", "key": "Control", "down": True})
    backend.apply({"action": "key", "key": "a", "down": True})
    backend.apply({"action": "text", "text": "مرحبا"})
    assert keyboard.released == ["a", "CTRL"] and mouse.released == ["L"]
    assert keyboard.typed == ["مرحبا"]
    backend.release()
    assert mouse.released == ["L"]
    assert not valid_input({"action": "move", "x": float("nan"), "y": 1})
    assert not valid_input({"action": "move", "x": True, "y": 1})
    assert not valid_input({"action": "move", "x": 10 ** 400, "y": 1})


def test_shortcuts_use_virtual_keys_and_always_release_modifiers():
    mouse, keyboard = Device(), Device()
    backend = InputState(mouse, keyboard, {}, {"Control": "CTRL", "KeyC": "VK_C"}, lambda: (0, 0, 100, 100))
    backend.apply({"action": "shortcut", "name": "copy"})
    assert keyboard.pressed == ["CTRL", "VK_C"]
    assert keyboard.released == ["VK_C", "CTRL"]
    assert not backend.held_keys
    assert valid_input({"action": "shortcut", "name": "files"})
    assert not valid_input({"action": "shortcut", "name": "arbitrary-command"})
    assert not valid_input({"action": "shortcut", "name": []})

    def fail(key):
        raise OSError("device unavailable")
    keyboard.press = fail
    with pytest.raises(OSError):
        backend.apply({"action": "shortcut", "name": "copy"})
    assert keyboard.released[-1] == "CTRL" and not backend.held_keys


def test_shortcut_protocol_reaches_input_backend(client):
    _, fake = enable(client)
    with client.websocket_connect("/api/desktop/control", headers=LOCAL) as socket:
        socket.receive_json()
        socket.send_json({"action": "shortcut", "name": "files"})
        flush(socket)
    assert fake.events == [{"action": "shortcut", "name": "files"}]
