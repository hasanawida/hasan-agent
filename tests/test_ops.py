"""Usage tracking, local-only guard, editor launch, env file and the `hassan` CLI."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from hassan_ai import config
from hassan_ai.config import load_env_file

from .conftest import wait

ROOT = Path(__file__).resolve().parents[1]


def test_usage_is_recorded_per_task_and_period(client):
    t = client.post("/api/tasks", json={"prompt": "count my usage", "mode": "fast"}).json()
    task = wait(client, t["id"])
    assert task["usage"]["calls"] == 4
    assert task["usage"]["input_tokens"] > 0
    assert all(o["input_tokens"] > 0 for o in task["outputs"])
    usage = client.get("/api/usage").json()
    assert usage["today"]["calls"] == 4 and usage["today"]["tasks"] == 1
    assert usage["week"]["by_model"]


def test_foreign_host_and_cross_site_posts_blocked(client):
    # a DNS-rebinding page (foreign Host, no key) gets nothing
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 401
    r = client.post("/api/tasks", json={"prompt": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/tasks", json={"prompt": "x", "mode": "fast"}, headers={"origin": "http://testserver"})
    assert ok.status_code == 201


PHONE = {"host": "my-pc.tail1234.ts.net", "x-forwarded-for": "100.64.0.7", "x-forwarded-proto": "https"}


def test_phone_needs_key_then_works(client):
    key = client.app.state.access_key
    # without the key: API refuses, pages redirect to the login page
    assert client.get("/api/tasks", headers=PHONE).status_code == 401
    r = client.get("/", headers=PHONE, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "6 أرقام" in client.get("/login", headers=PHONE).text
    assert client.get("/login?key=wrong", headers=PHONE).status_code == 401
    # scanning the QR = GET /login?key=... sets a long-lived, HttpOnly, Secure cookie
    r = client.get(f"/login?key={key}", headers=PHONE, follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=strict" in cookie.lower()
    client.cookies.set("hassan_key", key)
    t = client.post("/api/tasks", json={"prompt": "from my phone", "mode": "fast"},
                    headers={**PHONE, "origin": "https://my-pc.tail1234.ts.net"})
    assert t.status_code == 201
    assert wait(client, t.json()["id"])["status"] == "completed"
    # the pairing key/QR is never shown to a remote device, and it can't rotate it
    assert client.get("/api/remote", headers=PHONE).status_code == 403
    assert client.post("/api/remote/rotate", headers=PHONE).status_code == 403
    assert client.get("/api/whoami", headers=PHONE).json() == {"local": False}
    client.cookies.clear()


def test_proxy_cannot_pretend_to_be_local(client):
    # tailscale serve connects from 127.0.0.1; even with a local Host it stays remote
    spoof = {"host": "127.0.0.1:8787", "x-forwarded-for": "100.64.0.7"}
    assert client.get("/api/tasks", headers=spoof).status_code == 401


def test_bearer_key_and_rotation(client, tmp_path):
    key = client.app.state.access_key
    assert client.get("/api/tasks", headers={**PHONE, "authorization": f"Bearer {key}"}).status_code == 200
    info = client.get("/api/remote").json()
    assert info["enabled"] is False  # no public URL / LAN bind configured in tests
    assert client.post("/api/remote/rotate").status_code == 200
    assert client.get("/api/tasks", headers={**PHONE, "authorization": f"Bearer {key}"}).status_code == 401


def test_six_digit_pairing_code(client):
    code = client.post("/api/remote/pair-code").json()["code"]
    assert len(code) == 6
    assert client.post("/api/remote/pair-code", headers=PHONE).status_code == 401  # PC only
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/login", content=f"key={wrong}", headers={**PHONE, "content-type": "application/x-www-form-urlencoded"},
                       follow_redirects=False).status_code == 401
    r = client.post("/login", content=f"key={code}", headers={**PHONE, "content-type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    assert r.status_code == 303 and "hassan_session=" in r.headers["set-cookie"]
    assert client.app.state.access_key not in r.headers["set-cookie"]
    # single use
    again = client.post("/login", content=f"key={code}", headers={**PHONE, "content-type": "application/x-www-form-urlencoded"},
                        follow_redirects=False)
    assert again.status_code == 401
    client.cookies.clear()


def test_pairing_code_locks_after_wrong_tries():
    from hassan_ai.remote import PairingCodes
    p = PairingCodes()
    code = p.new()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert not p.redeem(wrong)
    assert not p.redeem(code)  # locked


def test_pairing_qr_when_public_url_set(tmp_path):
    from fastapi.testclient import TestClient
    from hassan_ai.server import create_app
    from .conftest import make_settings
    settings = make_settings(tmp_path)
    settings.public_url = "https://my-pc.tail1234.ts.net"
    with TestClient(create_app(settings)) as c:
        info = c.get("/api/remote").json()
        (url,) = info["urls"]
        assert url["pair_url"].startswith("https://my-pc.tail1234.ts.net/login?key=")
        assert url["qr_html"].startswith('<img src="data:image/png;base64,')
        assert c.get("/manifest.webmanifest", headers=PHONE).json()["short_name"] == "Hassan AI"


def test_open_in_editor(client, tmp_path, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, argv, **kw):
            calls.append(argv)

    monkeypatch.setattr("hassan_ai.server.subprocess.Popen", FakePopen)
    monkeypatch.setattr("hassan_ai.server.shutil.which", lambda cmd: "/usr/bin/code")
    f = tmp_path / "a.py"
    f.write_text("x")
    assert client.post("/api/open", json={"path": str(f), "line": 3}).status_code == 200
    assert calls[-1] == ["/usr/bin/code", "-g", f"{f.resolve()}:3"]
    assert client.post("/api/open", json={"path": "/etc"}).status_code == 403
    assert client.post("/api/open", json={"path": str(tmp_path / "missing")}).status_code == 404


def test_editor_cmd_shim_bypassed_on_windows_layout(tmp_path):
    from hassan_ai.server import editor_argv
    code_dir = tmp_path / "Microsoft VS Code"
    (code_dir / "bin").mkdir(parents=True)
    (code_dir / "resources" / "app" / "out").mkdir(parents=True)
    (code_dir / "Code.exe").write_text("")
    (code_dir / "resources" / "app" / "out" / "cli.js").write_text("")
    argv, env = editor_argv(str(code_dir / "bin" / "code.cmd"), ["C:\\a & b"])
    assert argv[0].endswith("Code.exe") and argv[1].endswith("cli.js") and argv[2] == "C:\\a & b"
    assert env["ELECTRON_RUN_AS_NODE"] == "1"


def test_env_file_does_not_override_real_env(tmp_path, monkeypatch):
    f = tmp_path / "hassan.env"
    f.write_text("# comment\nHASSAN_TEST_A=from-file\nHASSAN_TEST_B=\"quoted\"\n", encoding="utf-8")
    monkeypatch.setenv("HASSAN_TEST_A", "from-env")
    monkeypatch.delenv("HASSAN_TEST_B", raising=False)
    load_env_file(f)
    assert os.environ["HASSAN_TEST_A"] == "from-env"
    assert os.environ["HASSAN_TEST_B"] == "quoted"
    monkeypatch.delenv("HASSAN_TEST_B")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_cli_start_ask_status_stop(tmp_path, py_project):
    port = free_port()
    env = {**os.environ, "HASSAN_PORT": str(port), "HASSAN_DATA_DIR": str(tmp_path / "data"),
           "HASSAN_AI_MODE": "mock", "HASSAN_ALLOWED_ROOTS": str(tmp_path), "PYTHONPATH": str(ROOT)}
    run = lambda *a: subprocess.run([sys.executable, "-m", "hassan_ai", *a], cwd=py_project, env=env,  # noqa: E731
                                    capture_output=True, text=True, encoding="utf-8", timeout=90)
    try:
        assert "running" in run("start").stdout
        assert "mock" in run("status").stdout
        out = run("ask", "افحص", "المشروع", "--no-browser").stdout
        assert "sent" in out
        task_id = out.split()[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            task = httpx.get(f"{base}/api/tasks/{task_id}").json()
            if task["status"] == "completed":
                break
            time.sleep(0.2)
        assert task["status"] == "completed"
        assert Path(task["workspace"]) == py_project.resolve()  # workspace = folder `ask` ran in
    finally:
        assert "stopped" in run("stop").stdout
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            time.sleep(0.2)
        except httpx.HTTPError:
            break
    else:
        raise AssertionError("server still running after stop")
