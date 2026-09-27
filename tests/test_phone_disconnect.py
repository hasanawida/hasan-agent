"""ASGI disconnect regressions: never leave a cancelled phone poll waiting for input."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from hassan_ai.phone import PhoneHub, attach_phone_routes

META = {"control_enabled": True, "screen_enabled": True, "width": 1080, "height": 2400}


def test_http_disconnect_removes_waiter_before_next_command(tmp_path):
    async def scenario():
        hub = PhoneHub(tmp_path)
        hub.poll_seconds = 20
        paired = await hub.enroll(hub.create_pair_code()["code"], "Fake Android")
        ident = paired["device_id"]
        app = FastAPI()
        settings = SimpleNamespace(allowed_hosts=["testserver"], trusted_clients=["testclient"], public_url="")
        # Match the server's BaseHTTPMiddleware receive wrapper, not just a bare route.
        @app.middleware("http")
        async def pass_through(request, call_next):
            return await call_next(request)
        attach_phone_routes(app, settings, hub)
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": json.dumps(META).encode(), "more_body": False})
        sent = []
        async def send(message): sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
                 "method": "POST", "scheme": "https", "path": "/api/phone/poll", "raw_path": b"/api/phone/poll",
                 "query_string": b"", "root_path": "", "server": ("testserver", 443), "client": ("testclient", 1000),
                 "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                             (b"authorization", ("Bearer " + paired["token"]).encode())]}
        request = asyncio.create_task(app(scope, incoming.get, send))
        async def wait_poll():
            while not hub.devices[ident].polling: await asyncio.sleep(0)
        await asyncio.wait_for(wait_poll(), 1)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(request, 1)
        assert hub.devices[ident].polling is False
        assert hub.devices[ident].queue.empty()
        # A new command must go to the fresh poll, never the abandoned request.
        await hub.claim(ident, "manual:fresh")
        command_task = asyncio.create_task(hub.command(ident, "manual:fresh", "tap", {"x": .5, "y": .5}))
        await asyncio.sleep(0)
        reply = await asyncio.wait_for(hub.poll(ident, META), .2)
        command = reply["command"]
        assert command is not None and command["action"] == "tap"
        await hub.result(ident, {"id": command["id"], "ok": True, "result": {"tapped": True}})
        assert (await command_task)["ok"] is True
        await hub.close()
    asyncio.run(scenario())


def test_cancelling_asgi_request_cleans_child_poll_and_disconnect_waiter(tmp_path):
    from hassan_ai.phone import _poll_until_disconnect
    async def scenario():
        hub = PhoneHub(tmp_path)
        hub.poll_seconds = 20
        paired = await hub.enroll(hub.create_pair_code()["code"], "Fake Android")
        ident = paired["device_id"]
        receive_cancelled = asyncio.Event()
        class Request:
            async def receive(self):
                try: await asyncio.Future()
                finally: receive_cancelled.set()
        request = asyncio.create_task(_poll_until_disconnect(Request(), hub, ident, META))
        async def wait_poll():
            while not hub.devices[ident].polling: await asyncio.sleep(0)
        await asyncio.wait_for(wait_poll(), 1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError): await request
        assert not hub.devices[ident].polling
        assert receive_cancelled.is_set()
        await hub.close()
    asyncio.run(scenario())


def test_regular_poll_timeout_cleans_disconnect_listener(tmp_path):
    from hassan_ai.phone import _poll_until_disconnect
    async def scenario():
        hub = PhoneHub(tmp_path)
        hub.poll_seconds = .001
        paired = await hub.enroll(hub.create_pair_code()["code"], "Fake Android")
        receive_cancelled = asyncio.Event()
        class Request:
            async def receive(self):
                try: await asyncio.Future()
                finally: receive_cancelled.set()
        assert await _poll_until_disconnect(Request(), hub, paired["device_id"], META) == {"command": None}
        assert receive_cancelled.is_set()
        assert not hub.devices[paired["device_id"]].polling
        await hub.close()
    asyncio.run(scenario())
