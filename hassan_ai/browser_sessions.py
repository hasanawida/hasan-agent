"""Independent browser credentials; never persist raw cookies or the master key.

The server middleware owns login, legacy-cookie migration and authentication.
This module stores only token hashes and provides protected device management.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Callable

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from . import remote
from .desktop import same_origin

COOKIE = "hassan_session"
MAX_SESSIONS = 64
DEFAULT_TTL = 400 * 86400
MAX_REGISTRY_BYTES = 128 * 1024
SESSION_ID = re.compile(r"^browser_[0-9a-f]{24}$")
TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _label(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(not c.isprintable() for c in value):
        raise HTTPException(422, "Device label must contain 1..80 printable characters")
    return value.strip()


def _timestamp(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class BrowserSessionStore:
    """Single-process atomic registry, with bounded writes for last-seen updates.

    A malformed registry or failed write disables authentication for this store
    instance. Failed mutations raise 503 rather than claiming durable success.
    """

    def __init__(self, data_dir: Path, *, ttl_seconds: int = DEFAULT_TTL,
                 clock: Callable[[], float] = time.time, touch_interval: int = 300):
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if type(touch_interval) is not int or touch_interval < 0:
            raise ValueError("touch_interval must be nonnegative")
        self.path = Path(data_dir) / "browser-sessions.json"
        self.ttl_seconds, self.touch_interval = ttl_seconds, touch_interval
        self.clock = clock
        self.lock = threading.RLock()
        self.storage_error = False
        self._rows: dict[str, dict] = {}
        self._saved_seen: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > MAX_REGISTRY_BYTES:
                raise ValueError("Oversized browser registry")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"version", "devices"} or type(raw["version"]) is not int or raw["version"] != 1:
                raise ValueError("Invalid browser registry version")
            records = raw["devices"]
            if not isinstance(records, list) or len(records) > MAX_SESSIONS:
                raise ValueError("Invalid browser device list")
            rows, hashes = {}, set()
            for row in records:
                if not isinstance(row, dict) or set(row) != {"id", "label", "token_hash", "created_at", "last_seen", "expires_at", "legacy_migrated"}:
                    raise ValueError("Invalid browser session")
                ident, digest = row["id"], row["token_hash"]
                if not isinstance(ident, str) or not SESSION_ID.fullmatch(ident) or ident in rows:
                    raise ValueError("Invalid or duplicate browser id")
                if not isinstance(digest, str) or not TOKEN_HASH.fullmatch(digest) or digest in hashes:
                    raise ValueError("Invalid or duplicate token hash")
                if _label(row["label"]) != row["label"] or type(row["legacy_migrated"]) is not bool:
                    raise ValueError("Invalid browser metadata")
                if not all(_timestamp(row[k]) for k in ("created_at", "last_seen", "expires_at")):
                    raise ValueError("Invalid browser timestamps")
                if not row["created_at"] <= row["last_seen"] < row["expires_at"]:
                    raise ValueError("Invalid browser expiry")
                rows[ident] = dict(row)
                hashes.add(digest)
            self._rows = rows
            self._saved_seen = {key: value["last_seen"] for key, value in rows.items()}
        except (OSError, ValueError, TypeError, KeyError, RecursionError, HTTPException):
            self.storage_error = True
            self._rows = {}

    def _healthy(self) -> None:
        if self.storage_error:
            raise HTTPException(503, "Browser credential storage needs local repair")

    def _save(self, rows: dict[str, dict]) -> None:
        self._healthy()
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"version": 1, "devices": list(rows.values())}, ensure_ascii=False)
            if len(payload.encode("utf-8")) > MAX_REGISTRY_BYTES:
                raise OSError("Browser registry exceeds size limit")
            with temp.open("w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(self.path)
        except OSError as exc:
            # Do not acknowledge a revocation/issue whose durable write failed.
            # Existing on-disk data stays intact; this process rejects all tokens.
            self.storage_error = True
            self._rows = {}
            self._saved_seen = {}
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(503, "Could not save browser authorization") from exc
        self._rows = rows
        self._saved_seen = {key: value["last_seen"] for key, value in rows.items()}

    @staticmethod
    def _public(row: dict) -> dict:
        return {key: value for key, value in row.items() if key != "token_hash"}

    def issue(self, label: str, *, legacy_migrated: bool = False) -> str:
        label = _label(label)
        if type(legacy_migrated) is not bool:
            raise ValueError("legacy_migrated must be boolean")
        with self.lock:
            self._healthy()
            now = self.clock()
            rows = {key: dict(value) for key, value in self._rows.items() if value["expires_at"] > now}
            if len(rows) >= MAX_SESSIONS:
                raise HTTPException(409, "Remove an unused browser device before pairing another")
            token = secrets.token_urlsafe(32)
            ident = "browser_" + secrets.token_hex(12)
            rows[ident] = {"id": ident, "label": label, "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                           "created_at": now, "last_seen": now, "expires_at": now + self.ttl_seconds,
                           "legacy_migrated": legacy_migrated}
            self._save(rows)
            return token

    def authenticate(self, token: str | None) -> dict | None:
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            if self.storage_error:
                return None
            now = self.clock()
            for ident, row in self._rows.items():
                if hmac.compare_digest(row["token_hash"], digest) and row["expires_at"] > now:
                    row["last_seen"] = max(row["last_seen"], now)
                    if now - self._saved_seen.get(ident, 0) >= self.touch_interval:
                        try:
                            self._save({key: dict(value) for key, value in self._rows.items()})
                        except HTTPException:
                            return None
                    return self._public(row)
            return None

    def list_public(self) -> list[dict]:
        with self.lock:
            self._healthy()
            now = self.clock()
            return sorted((self._public(row) for row in self._rows.values() if row["expires_at"] > now),
                          key=lambda row: row["created_at"], reverse=True)

    def rename(self, ident: str, label: str) -> dict:
        label = _label(label)
        with self.lock:
            self._healthy()
            row = self._rows.get(ident)
            if row is None or row["expires_at"] <= self.clock():
                raise HTTPException(404, "Browser device not found")
            rows = {key: dict(value) for key, value in self._rows.items()}
            rows[ident]["label"] = label
            self._save(rows)
            return self._public(rows[ident])

    def revoke(self, ident: str) -> bool:
        with self.lock:
            self._healthy()
            if ident not in self._rows:
                return False
            self._save({key: dict(value) for key, value in self._rows.items() if key != ident})
            return True

    def revoke_all(self) -> int:
        with self.lock:
            self._healthy()
            count = len(self._rows)
            self._save({})
            return count


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr | None = Field(default=None, pattern=r"^browser_[0-9a-f]{24}$")
    label: StrictStr = Field(min_length=1, max_length=80)


def attach_browser_session_routes(app, settings, store: BrowserSessionStore) -> None:
    """Attach routes behind the app's existing authentication middleware.

    GET /api/devices -> {devices, local, current_id}; POST /api/devices/rename
    accepts {label, id?}. A remote browser can rename only its current cookie.
    POST /api/devices/{id}/revoke is local-only and requires exact Origin.
    """
    app.state.browser_sessions = store

    def local(request: Request) -> bool:
        return remote.is_local(request, settings.allowed_hosts, settings.trusted_clients)

    def current(request: Request) -> dict | None:
        candidate = request.cookies.get(COOKIE)
        if candidate is not None:
            return store.authenticate(candidate)
        # Middleware may have just migrated a legacy credential on this request.
        session = getattr(request.state, "browser_session", None)
        return session if isinstance(session, dict) and SESSION_ID.fullmatch(str(session.get("id", ""))) else None

    @app.get("/api/devices")
    async def devices(request: Request):
        active = current(request)
        return {"devices": store.list_public(), "local": local(request), "current_id": active["id"] if active else None}

    @app.post("/api/devices/rename")
    async def rename(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "Same-origin request required")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2048:
                raise HTTPException(413, "Device metadata is too large")
        try:
            payload = RenameBody.model_validate_json(bytes(body))
        except ValidationError as exc:
            raise HTTPException(422, "Invalid device metadata") from exc
        active = current(request)
        ident = payload.id or (active["id"] if active else None)
        if ident is None:
            raise HTTPException(422, "Choose the browser device to rename")
        if not local(request) and (active is None or active["id"] != ident):
            raise HTTPException(403, "Only this browser may rename its device")
        return {"device": store.rename(ident, payload.label)}

    @app.post("/api/devices/{ident}/revoke")
    async def revoke(ident: str, request: Request):
        if not local(request) or not same_origin(request):
            raise HTTPException(403, "Revoke browser devices from the local computer")
        if not SESSION_ID.fullmatch(ident) or not store.revoke(ident):
            raise HTTPException(404, "Browser device not found")
        return {"revoked": True, "id": ident}
