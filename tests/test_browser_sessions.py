"""Per-browser credentials: persistence, isolation, bounded metadata and route boundaries."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hassan_ai import remote
from hassan_ai.browser_sessions import (
    BrowserSessionStore, COOKIE, MAX_REGISTRY_BYTES, MAX_SESSIONS, attach_browser_session_routes,
)

LOCAL = {"origin": "http://testserver"}
REMOTE = {"host": "pc.ts.net", "origin": "https://pc.ts.net", "x-forwarded-for": "100.64.0.8", "x-forwarded-proto": "https"}


def test_unique_browser_credentials_are_hash_only_and_survive_restart(tmp_path):
    store = BrowserSessionStore(tmp_path)
    first = store.issue("هاتفي", legacy_migrated=True)
    second = store.issue("Tablet")
    assert first != second
    one, two = store.authenticate(first), store.authenticate(second)
    assert one["id"] != two["id"] and one["legacy_migrated"] is True
    assert two["legacy_migrated"] is False
    raw = store.path.read_text(encoding="utf-8")
    assert first not in raw and second not in raw
    assert hashlib.sha256(first.encode()).hexdigest() in raw
    assert "token_hash" not in one and "token_hash" not in json.dumps(store.list_public())
    assert set(one) == {"id", "label", "created_at", "last_seen", "expires_at", "legacy_migrated"}
    restored = BrowserSessionStore(tmp_path)
    assert restored.authenticate(first)["id"] == one["id"]
    assert restored.authenticate(second)["id"] == two["id"]
    one["label"] = "Mutated copy"
    assert store.authenticate(first)["label"] == "هاتفي"


def test_revoke_is_per_device_and_persistent_then_revoke_all(tmp_path):
    store = BrowserSessionStore(tmp_path)
    first, second = store.issue("One"), store.issue("Two")
    ident = store.authenticate(first)["id"]
    assert store.revoke(ident) is True
    assert store.revoke(ident) is False
    assert store.authenticate(first) is None
    assert store.authenticate(second) is not None
    restored = BrowserSessionStore(tmp_path)
    assert restored.authenticate(first) is None
    assert restored.authenticate(second) is not None
    assert restored.revoke_all() == 1
    assert BrowserSessionStore(tmp_path).authenticate(second) is None


def test_expiry_is_absolute_and_cannot_be_extended_by_activity(tmp_path):
    now = [100.0]
    store = BrowserSessionStore(tmp_path, ttl_seconds=20, clock=lambda: now[0], touch_interval=5)
    token = store.issue("Phone")
    now[0] = 119
    assert store.authenticate(token)["expires_at"] == 120
    now[0] = 120
    assert store.authenticate(token) is None
    assert store.list_public() == []
    assert BrowserSessionStore(tmp_path, clock=lambda: now[0]).authenticate(token) is None


def test_last_seen_is_visible_immediately_but_disk_writes_are_throttled(tmp_path):
    now = [100.0]
    store = BrowserSessionStore(tmp_path, clock=lambda: now[0], touch_interval=10)
    token = store.issue("Phone")
    raw = store.path.read_text(encoding="utf-8")
    now[0] = 105
    assert store.authenticate(token)["last_seen"] == 105
    assert store.path.read_text(encoding="utf-8") == raw
    now[0] = 110
    assert store.authenticate(token)["last_seen"] == 110
    restored = BrowserSessionStore(tmp_path, clock=lambda: now[0])
    assert restored.list_public()[0]["last_seen"] == 110
    # A backwards clock never lowers the visible last-seen timestamp.
    now[0] = 109
    assert store.authenticate(token)["last_seen"] == 110


def test_capacity_is_bounded_and_expired_slots_can_be_reused(tmp_path):
    now = [100.0]
    store = BrowserSessionStore(tmp_path, ttl_seconds=5, clock=lambda: now[0])
    for index in range(MAX_SESSIONS):
        store.issue(f"Device {index}")
    with pytest.raises(HTTPException) as exc:
        store.issue("Overflow")
    assert exc.value.status_code == 409
    now[0] = 105
    assert store.authenticate(store.issue("Replacement"))["label"] == "Replacement"
    assert len(store.list_public()) == 1


def test_concurrent_pairing_does_not_lose_credentials(tmp_path):
    store = BrowserSessionStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda index: store.issue(f"Phone {index}"), range(8)))
    restored = BrowserSessionStore(tmp_path)
    assert len(restored.list_public()) == 8
    assert all(restored.authenticate(token) is not None for token in tokens)


@pytest.mark.parametrize("bad", [None, "", "x" * 1000, "not a token", "🔐" * 40, 12, True, "x" * 43])
def test_invalid_or_unknown_tokens_do_not_authenticate(tmp_path, bad):
    store = BrowserSessionStore(tmp_path)
    good = store.issue("Phone")
    assert store.authenticate(bad) is None
    assert store.authenticate(good) is not None


@pytest.mark.parametrize("label", ["", "  ", "x" * 81, "A\nB", "A\x00B", None, 123])
def test_invalid_labels_cannot_enter_storage(tmp_path, label):
    store = BrowserSessionStore(tmp_path)
    with pytest.raises(HTTPException) as exc:
        store.issue(label)
    assert exc.value.status_code == 422
    assert not store.path.exists()


@pytest.mark.parametrize("corruption", ["json", "version", "hash", "duplicate", "timestamp", "extra", "oversize"])
def test_malformed_registry_fails_closed_without_overwriting(tmp_path, corruption):
    original = BrowserSessionStore(tmp_path)
    token = original.issue("Phone")
    raw = json.loads(original.path.read_text(encoding="utf-8"))
    if corruption == "version": raw["version"] = True
    elif corruption == "hash": raw["devices"][0]["token_hash"] = token
    elif corruption == "duplicate": raw["devices"].append(dict(raw["devices"][0]))
    elif corruption == "timestamp": raw["devices"][0]["expires_at"] = float("nan")
    elif corruption == "extra": raw["devices"][0]["ip"] = "not permitted metadata"
    value = "{" if corruption == "json" else "x" * (MAX_REGISTRY_BYTES + 1) if corruption == "oversize" else json.dumps(raw)
    original.path.write_text(value, encoding="utf-8")
    store = BrowserSessionStore(tmp_path)
    assert store.storage_error and store.authenticate(token) is None
    with pytest.raises(HTTPException) as exc:
        store.issue("New")
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException): store.list_public()
    with pytest.raises(HTTPException): store.revoke_all()
    assert original.path.read_text(encoding="utf-8") == value


def test_failed_atomic_revocation_is_not_acknowledged_and_disables_store(tmp_path, monkeypatch):
    store = BrowserSessionStore(tmp_path)
    token = store.issue("Phone")
    ident = store.authenticate(token)["id"]
    before = store.path.read_bytes()
    def fail_replace(self, target):
        raise OSError("simulated disk error")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(HTTPException) as exc:
        store.revoke(ident)
    assert exc.value.status_code == 503
    assert store.authenticate(token) is None and store.storage_error
    assert store.path.read_bytes() == before  # No half-written registry or false durable-success claim.
    assert not store.path.with_name(store.path.name + ".tmp").exists()


def test_failed_touch_write_fails_authentication_closed(tmp_path, monkeypatch):
    now = [10.0]
    store = BrowserSessionStore(tmp_path, clock=lambda: now[0], touch_interval=1)
    token = store.issue("Phone")
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    now[0] = 12
    assert store.authenticate(token) is None
    assert store.storage_error


def test_rename_is_persistent_without_changing_credential(tmp_path):
    store = BrowserSessionStore(tmp_path)
    token = store.issue("Original")
    ident = store.authenticate(token)["id"]
    assert store.rename(ident, "  هاتفي الجديد  ")["label"] == "هاتفي الجديد"
    assert BrowserSessionStore(tmp_path).authenticate(token)["label"] == "هاتفي الجديد"


@pytest.fixture
def browser_client(tmp_path):
    store = BrowserSessionStore(tmp_path)
    settings = SimpleNamespace(allowed_hosts=["testserver"], trusted_clients=["testclient"])
    app = FastAPI()
    @app.middleware("http")
    async def guard(request: Request, call_next):
        local = remote.is_local(request, settings.allowed_hosts, settings.trusted_clients)
        session = store.authenticate(request.cookies.get(COOKIE))
        if not local and session is None:
            return JSONResponse({"detail": "Browser session required"}, status_code=401)
        request.state.browser_session = session
        return await call_next(request)
    attach_browser_session_routes(app, settings, store)
    with TestClient(app) as client:
        yield client, store


def test_list_exposes_only_public_metadata_and_current_identity(browser_client):
    client, store = browser_client
    token = store.issue("Phone")
    ident = store.authenticate(token)["id"]
    client.cookies.set(COOKIE, token)
    body = client.get("/api/devices", headers=REMOTE).json()
    assert body["local"] is False and body["current_id"] == ident
    assert body["devices"][0]["label"] == "Phone"
    assert "token_hash" not in json.dumps(body) and token not in json.dumps(body)
    assert client.get("/api/devices").json()["local"] is True


def test_revoke_is_local_and_requires_exact_origin(browser_client):
    client, store = browser_client
    token = store.issue("Phone")
    ident = store.authenticate(token)["id"]
    client.cookies.set(COOKIE, token)
    path = f"/api/devices/{ident}/revoke"
    assert client.post(path, headers=REMOTE).status_code == 403
    assert client.post(path).status_code == 403
    assert client.post(path, headers={"origin": "http://testserver:9999"}).status_code == 403
    assert client.post(path, headers=LOCAL).json()["revoked"] is True
    assert client.get("/api/devices", headers=REMOTE).status_code == 401
    assert BrowserSessionStore(store.path.parent).authenticate(token) is None


def test_remote_can_rename_only_its_own_session_local_can_choose(browser_client):
    client, store = browser_client
    token, other = store.issue("Phone"), store.issue("Tablet")
    ident = store.authenticate(other)["id"]
    client.cookies.set(COOKIE, token)
    assert client.post("/api/devices/rename", headers=REMOTE, json={"label": "My phone"}).json()["device"]["label"] == "My phone"
    assert client.post("/api/devices/rename", headers=REMOTE, json={"id": ident, "label": "Other"}).status_code == 403
    assert client.post("/api/devices/rename", headers=LOCAL, json={"id": ident, "label": "My tablet"}).status_code == 200
    assert store.authenticate(other)["label"] == "My tablet"
    assert client.post("/api/devices/rename", json={"label": "No origin"}).status_code == 403
    assert client.post("/api/devices/rename", headers=LOCAL, json={"label": "x", "ip": "forbidden"}).status_code == 422
    assert client.post("/api/devices/rename", headers=LOCAL, content=b"x" * 2049).status_code == 413
