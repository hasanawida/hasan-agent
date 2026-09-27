"""Outbound-only Android companion transport with per-device authorization.

The phone initiates HTTPS polls; the host never exposes ADB or an arbitrary shell.
Credentials survive restart, while screen frames, grants, leases and commands do not.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from . import remote
from .desktop import same_origin

PHONE_PUBLIC_PATHS = frozenset({"/api/phone/enroll", "/api/phone/poll", "/api/phone/result", "/api/phone/frame"})
MAX_FRAME_BYTES = 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_DEVICES = 32
DEVICE_ID = re.compile(r"^phone_[0-9a-f]{24}$")
TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")
OWNER = re.compile(r"^[A-Za-z0-9_:\-]{1,128}$")


@dataclass
class Pending:
    id: str
    owner: str
    action: str
    args: dict
    deadline: float
    expires_at: int
    future: asyncio.Future
    delivered: bool = False

    def public(self) -> dict:
        return {"id": self.id, "action": self.action, "args": self.args, "expires_at": self.expires_at}


@dataclass
class PhoneDevice:
    device_id: str
    label: str
    token_hash: str
    last_seen: float = 0.0
    seen_at: float | None = None
    control_enabled: bool = False
    screen_enabled: bool = False
    width: int = 0
    height: int = 0
    frame: bytes | None = None
    frame_at: float = 0.0
    owner: str | None = None
    owner_kind: str | None = None
    lease_until: float = 0.0
    pending: Pending | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    polling: bool = False


class PhoneHub:
    """Shared manual/agent ownership and a single non-replayable command per phone."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "phones.json"
        self.devices: dict[str, PhoneDevice] = {}
        self.lock = asyncio.Lock()
        self.online_seconds = 45.0
        self.lease_seconds = 45.0
        self.command_seconds = 15.0
        self.poll_seconds = 20.0
        self.pair_seconds = 300.0
        self.pair_code: str | None = None
        self.pair_until = 0.0
        self.pair_failures = 0
        self.closed = False
        self.storage_error = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > MAX_JSON_BYTES:
                raise ValueError("Oversized phone registry")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw["devices"]
            if not isinstance(rows, list) or len(rows) > MAX_DEVICES:
                raise ValueError("Invalid phone registry")
            devices = {}
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"device_id", "label", "token_hash"}:
                    raise ValueError("Invalid phone record")
                ident, label, token_hash = row["device_id"], row["label"], row["token_hash"]
                if (not isinstance(ident, str) or not DEVICE_ID.fullmatch(ident)
                        or not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
                        or not isinstance(token_hash, str) or not TOKEN_HASH.fullmatch(token_hash)
                        or ident in devices):
                    raise ValueError("Invalid phone credential")
                devices[ident] = PhoneDevice(ident, label, token_hash)
            self.devices = devices
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            # Fail closed; do not overwrite a malformed registry while enrolling.
            self.devices = {}
            self.storage_error = True

    def _save(self, devices: dict[str, PhoneDevice]) -> None:
        if self.storage_error:
            raise HTTPException(503, "Phone credentials need local repair")
        payload = {"devices": [{"device_id": d.device_id, "label": d.label, "token_hash": d.token_hash}
                               for d in devices.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(self.path)
        except OSError as exc:
            raise HTTPException(503, "Could not save phone authorization") from exc

    def _device(self, device_id: str) -> PhoneDevice:
        if self.closed:
            raise HTTPException(503, "Phone service is stopped")
        device = self.devices.get(device_id)
        if device is None:
            raise HTTPException(404, "Phone not found")
        return device

    def authenticate(self, token: str) -> str:
        if self.closed:
            raise HTTPException(503, "Phone service is stopped")
        if not token or not 32 <= len(token) <= 128:
            raise HTTPException(401, "Phone token required")
        digest = hashlib.sha256(token.encode()).hexdigest()
        for device in self.devices.values():
            if hmac.compare_digest(digest, device.token_hash):
                return device.device_id
        raise HTTPException(401, "Phone token required")

    def create_pair_code(self) -> dict:
        if self.closed or self.storage_error:
            raise HTTPException(503, "Phone service unavailable")
        if len(self.devices) >= MAX_DEVICES:
            raise HTTPException(409, "Remove an unused phone before pairing another")
        self.pair_code = f"{secrets.randbelow(1_000_000):06d}"
        self.pair_until = time.monotonic() + self.pair_seconds
        self.pair_failures = 0
        return {"code": self.pair_code, "expires_in": int(self.pair_seconds)}

    async def enroll(self, code: str, label: str) -> dict:
        async with self.lock:
            if self.closed or self.storage_error:
                raise HTTPException(503, "Phone service unavailable")
            if not self.pair_code or time.monotonic() >= self.pair_until or self.pair_failures >= 5:
                self.pair_code = None
                raise HTTPException(403, "Pairing code expired or unavailable")
            if not hmac.compare_digest(code.encode(), self.pair_code.encode()):
                self.pair_failures += 1
                if self.pair_failures >= 5:
                    self.pair_code = None
                raise HTTPException(403, "Pairing code is incorrect")
            if len(self.devices) >= MAX_DEVICES:
                raise HTTPException(409, "Too many paired phones")
            token = secrets.token_urlsafe(32)
            ident = "phone_" + secrets.token_hex(12)
            device = PhoneDevice(ident, label.strip(), hashlib.sha256(token.encode()).hexdigest())
            updated = {**self.devices, ident: device}
            self._save(updated)
            self.devices = updated
            self.pair_code = None
            return {"device_id": ident, "token": token}

    @staticmethod
    def _cancel(device: PhoneDevice, detail: str, code: int = 409) -> None:
        pending, device.pending = device.pending, None
        if pending and not pending.future.done():
            # Complete with a value so a cancelled HTTP waiter cannot leave an unhandled Future exception.
            pending.future.set_result({"failure": (code, detail)})
        while not device.queue.empty():
            device.queue.get_nowait()
        if device.polling:
            device.queue.put_nowait(None)

    def _end_lease(self, device: PhoneDevice, detail: str) -> None:
        self._cancel(device, detail)
        device.owner = device.owner_kind = None
        device.lease_until = 0.0

    def _expire(self, device: PhoneDevice) -> bool:
        now = time.monotonic()
        online = device.seen_at is not None and now - device.last_seen < self.online_seconds
        if not online:
            device.control_enabled = device.screen_enabled = False
            device.frame = None
            self._end_lease(device, "Phone disconnected; command was not retried")
        elif device.owner and now >= device.lease_until:
            self._end_lease(device, "Phone control session expired")
        if device.frame and now - device.frame_at >= self.online_seconds:
            device.frame = None
        return online

    def list_devices(self) -> list[dict]:
        rows = []
        for device in self.devices.values():
            online = self._expire(device)
            rows.append({"id": device.device_id, "device_id": device.device_id, "label": device.label,
                         "online": online, "control_enabled": device.control_enabled,
                         "screen_enabled": device.screen_enabled, "width": device.width,
                         "height": device.height, "last_seen": device.seen_at,
                         "owner_kind": device.owner_kind, "busy": bool(device.owner),
                         "frame_available": bool(device.frame and device.screen_enabled and online)})
        return rows

    async def claim(self, device_id: str, owner: str, kind: str = "manual") -> str:
        if not isinstance(owner, str) or not OWNER.fullmatch(owner) or kind not in {"manual", "agent"}:
            raise HTTPException(422, "Invalid control owner")
        async with self.lock:
            device = self._device(device_id)
            if not self._expire(device):
                raise HTTPException(409, "Phone is offline")
            if not device.control_enabled:
                raise HTTPException(403, "Enable control on the phone first")
            if device.owner and (device.owner != owner or device.owner_kind != kind):
                raise HTTPException(409, "Phone is controlled by another session")
            device.owner, device.owner_kind = owner, kind
            device.lease_until = time.monotonic() + self.lease_seconds
            return owner

    async def renew(self, device_id: str, owner: str, kind: str = "agent") -> str:
        """Renew a live lease only; a heartbeat must never undo an explicit stop."""
        async with self.lock:
            device = self._device(device_id)
            if (not self._expire(device) or not device.control_enabled or device.owner != owner
                    or device.owner_kind != kind):
                raise HTTPException(409, "Phone control session ended")
            device.lease_until = time.monotonic() + self.lease_seconds
            return owner

    async def release(self, device_id: str, owner: str) -> None:
        async with self.lock:
            device = self._device(device_id)
            self._expire(device)
            if device.owner and device.owner != owner:
                raise HTTPException(403, "This session does not control the phone")
            self._end_lease(device, "Phone control stopped")

    async def command(self, device_id: str, owner: str, action: str, args: dict) -> dict:
        args = validate_action(action, args)
        async with self.lock:
            device = self._device(device_id)
            if not self._expire(device):
                raise HTTPException(409, "Phone is offline")
            if not device.control_enabled:
                raise HTTPException(403, "Enable control on the phone first")
            if not device.owner or device.owner != owner:
                raise HTTPException(403, "Claim this phone before sending a command")
            if action == "stop_screen" and device.owner_kind != "manual":
                raise HTTPException(403, "Only a manual session may stop phone sharing")
            if device.pending:
                raise HTTPException(409, "Wait for the current phone command")
            device.lease_until = time.monotonic() + self.lease_seconds
            pending = Pending("cmd_" + secrets.token_hex(12), owner, action, args,
                              time.monotonic() + self.command_seconds,
                              int((time.time() + self.command_seconds) * 1000),
                              asyncio.get_running_loop().create_future())
            device.pending = pending
            while not device.queue.empty():
                device.queue.get_nowait()
            device.queue.put_nowait(pending)
        try:
            result = await asyncio.wait_for(asyncio.shield(pending.future), self.command_seconds)
            if "failure" in result:
                code, detail = result["failure"]
                raise HTTPException(code, detail)
            return result
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Phone did not confirm this command; do not retry automatically") from exc
        finally:
            async with self.lock:
                if device.pending is pending:
                    self._cancel(device, "Phone command ended")

    async def poll(self, device_id: str, metadata: dict) -> dict:
        async with self.lock:
            device = self._device(device_id)
            self._expire(device)
            device.last_seen, device.seen_at = time.monotonic(), time.time()
            device.control_enabled = metadata["control_enabled"]
            device.screen_enabled = metadata["screen_enabled"]
            device.width, device.height = metadata["width"], metadata["height"]
            if not device.control_enabled:
                self._end_lease(device, "Control was disabled on the phone")
            if not device.screen_enabled:
                device.frame = None
            if device.polling:
                raise HTTPException(409, "Another phone poll is already waiting")
            # A cancellation marker from the prior request is not a command.
            while not device.queue.empty() and device.pending is None:
                device.queue.get_nowait()
            device.polling = True
        try:
            try:
                pending = await asyncio.wait_for(device.queue.get(), self.poll_seconds)
            except asyncio.TimeoutError:
                return {"command": None}
            async with self.lock:
                if (pending is None or self.devices.get(device_id) is not device or self.closed
                        or device.pending is not pending or pending.delivered
                        or time.monotonic() >= pending.deadline):
                    return {"command": None}
                if not self._expire(device) or not device.control_enabled or device.owner != pending.owner:
                    return {"command": None}
                pending.delivered = True
                return {"command": pending.public()}
        finally:
            device.polling = False

    async def result(self, device_id: str, body: dict) -> None:
        async with self.lock:
            device = self._device(device_id)
            self._expire(device)
            pending = device.pending
            if (not pending or pending.id != body["id"] or not pending.delivered
                    or time.monotonic() >= pending.deadline or pending.future.done()):
                raise HTTPException(409, "Command is no longer awaiting a result")
            if not device.control_enabled or device.owner != pending.owner:
                raise HTTPException(403, "Phone control is no longer authorized")
            if body["ok"] and pending.action == "stop_screen":
                device.screen_enabled = False
                device.frame = None
            pending.future.set_result(body)

    async def frame(self, device_id: str, data: bytes) -> None:
        if len(data) > MAX_FRAME_BYTES:
            raise HTTPException(413, "Phone frame is too large")
        if len(data) < 8 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
            raise HTTPException(415, "A JPEG frame is required")
        async with self.lock:
            device = self._device(device_id)
            if not self._expire(device) or not device.screen_enabled:
                raise HTTPException(403, "Enable screen sharing on the phone first")
            device.frame, device.frame_at = data, time.monotonic()

    def get_frame(self, device_id: str) -> bytes:
        device = self._device(device_id)
        if not self._expire(device) or not device.screen_enabled or not device.frame:
            raise HTTPException(404, "No current phone frame")
        return device.frame

    async def revoke(self, device_id: str) -> None:
        async with self.lock:
            device = self._device(device_id)
            # Disable the in-memory credential even if durable storage fails.
            updated = {k: v for k, v in self.devices.items() if k != device_id}
            self.devices = updated
            self._end_lease(device, "Phone authorization revoked")
            device.frame = None
            self._save(updated)

    async def revoke_all(self) -> None:
        async with self.lock:
            for device in self.devices.values():
                self._end_lease(device, "Phone authorization revoked")
                device.frame = None
            self.devices = {}
            self.pair_code = None
            self._save({})

    async def close(self) -> None:
        async with self.lock:
            self.closed = True
            self.pair_code = None
            for device in self.devices.values():
                self._end_lease(device, "Phone service stopped")
                device.frame = None
                device.control_enabled = device.screen_enabled = False


def validate_action(action: str, args: Any) -> dict:
    if not isinstance(action, str) or not isinstance(args, dict):
        raise HTTPException(422, "Command name and arguments are invalid")
    good = False
    if action in {"inspect", "stop_screen"}:
        good = not args
    elif action == "tap":
        good = set(args) == {"x", "y"} and _coordinates(args, ("x", "y"))
    elif action == "swipe":
        duration = args.get("duration_ms", 300)
        good = (set(args) <= {"x1", "y1", "x2", "y2", "duration_ms"}
                and _coordinates(args, ("x1", "y1", "x2", "y2"))
                and type(duration) is int and 50 <= duration <= 2000)
    elif action == "text":
        text = args.get("text")
        good = (set(args) == {"text"} and isinstance(text, str) and 0 < len(text) <= 1000
                and all(c.isprintable() or c in "\n\t" for c in text))
    elif action == "key":
        good = set(args) == {"key"} and args.get("key") in ("home", "back", "recents", "enter")
    if not good:
        raise HTTPException(422, "Unsupported phone command or invalid arguments")
    return dict(args)


def _coordinates(args: dict, names: tuple[str, ...]) -> bool:
    return all(type(args.get(k)) in (int, float) and 0 <= args[k] <= 1 and math.isfinite(args[k]) for k in names)


class PhoneBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollBody(PhoneBody):
    code: StrictStr = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    label: StrictStr = Field(min_length=1, max_length=80)


class PollBody(PhoneBody):
    control_enabled: StrictBool
    screen_enabled: StrictBool
    width: StrictInt = Field(ge=0, le=16384)
    height: StrictInt = Field(ge=0, le=16384)


class ResultBody(PhoneBody):
    id: StrictStr = Field(pattern=r"^cmd_[0-9a-f]{24}$")
    ok: StrictBool
    result: dict = Field(default_factory=dict)
    error: StrictStr | None = Field(default=None, max_length=2000)


class SessionBody(PhoneBody):
    owner: StrictStr | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_:\-]+$")


class StopBody(PhoneBody):
    owner: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:\-]+$")


class CommandBody(StopBody):
    action: StrictStr = Field(max_length=20)
    args: dict = Field(default_factory=dict)


async def _bounded_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if size < 0 or size > limit:
            raise HTTPException(413, "Request body is too large")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(413, "Request body is too large")
        data.extend(chunk)
    return bytes(data)


async def _json_body(request: Request, model, limit: int = MAX_JSON_BYTES):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "JSON body required")
    raw = await _bounded_body(request, limit)
    try:
        def invalid_constant(value):
            raise ValueError("Non-finite JSON number")
        return model.model_validate(json.loads(raw, parse_constant=invalid_constant))
    except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError) as exc:
        raise HTTPException(422, "Invalid phone request") from exc


def attach_phone_routes(app, settings, hub: PhoneHub) -> None:
    def secure(request: Request) -> None:
        local = remote.is_local(request, settings.allowed_hosts, settings.trusted_clients)
        if not local and not remote.is_https(request):
            raise HTTPException(403, "HTTPS is required for phone connections")

    def phone(request: Request) -> str:
        secure(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "Phone token required")
        return hub.authenticate(auth[7:].strip())

    def dashboard_write(request: Request, *, local: bool = False) -> None:
        secure(request)
        if not same_origin(request) or (local and not remote.is_local(request, settings.allowed_hosts,
                                                                        settings.trusted_clients)):
            raise HTTPException(403, "Use Hassan on this computer" if local else "Same-origin request required")

    @app.post("/api/phones/pair-code")
    async def pair_code(request: Request):
        dashboard_write(request, local=True)
        return {**hub.create_pair_code(), "server_url": settings.public_url}

    @app.post("/api/phone/enroll")
    async def enroll(request: Request):
        secure(request)
        body = await _json_body(request, EnrollBody, 4096)
        if not body.label.strip() or not all(c.isprintable() for c in body.label):
            raise HTTPException(422, "Phone name is required")
        return await hub.enroll(body.code, body.label)

    @app.post("/api/phone/poll")
    async def poll(request: Request):
        ident = phone(request)
        body = await _json_body(request, PollBody, 4096)
        return await hub.poll(ident, body.model_dump())

    @app.post("/api/phone/result")
    async def result(request: Request):
        ident = phone(request)
        body = await _json_body(request, ResultBody)
        await hub.result(ident, body.model_dump(exclude_none=True))
        return {"ok": True}

    @app.post("/api/phone/frame")
    async def frame(request: Request):
        ident = phone(request)
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "image/jpeg":
            raise HTTPException(415, "JPEG body required")
        await hub.frame(ident, await _bounded_body(request, MAX_FRAME_BYTES))
        return {"ok": True}

    @app.get("/api/phones")
    async def phones(request: Request):
        return {"devices": hub.list_devices(), "local": remote.is_local(request, settings.allowed_hosts,
                                                                       settings.trusted_clients)}

    @app.get("/api/phones/{device_id}/frame")
    async def phone_frame(device_id: str):
        return Response(hub.get_frame(device_id), media_type="image/jpeg", headers={
            "Cache-Control": "no-store, private", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/phones/{device_id}/session")
    async def session(device_id: str, request: Request):
        dashboard_write(request)
        body = await _json_body(request, SessionBody, 4096)
        if body.owner:
            device = hub._device(device_id)
            hub._expire(device)
            if device.owner != body.owner or device.owner_kind != "manual":
                raise HTTPException(403, "This session no longer controls the phone")
        owner = body.owner or secrets.token_urlsafe(24)
        await hub.claim(device_id, owner, "manual")
        return {"owner": owner}

    @app.post("/api/phones/{device_id}/session/stop")
    async def stop(device_id: str, request: Request):
        dashboard_write(request)
        body = await _json_body(request, StopBody, 4096)
        await hub.release(device_id, body.owner)
        return {"ok": True}

    @app.post("/api/phones/{device_id}/command")
    async def command(device_id: str, request: Request):
        dashboard_write(request)
        body = await _json_body(request, CommandBody)
        return await hub.command(device_id, body.owner, body.action, body.args)

    @app.post("/api/phones/{device_id}/revoke")
    async def revoke(device_id: str, request: Request):
        dashboard_write(request, local=True)
        await hub.revoke(device_id)
        return {"ok": True}
