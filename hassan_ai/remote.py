"""Phone / remote access.

Rules
-----
* A request is *local* only when it comes from a loopback address AND uses a
  local Host name. Local requests need no key (the PC's own browser/CLI).
* Everything else — the phone over Tailscale (`tailscale serve` proxies with the
  ts.net Host), or any device on the LAN — must present the access key, either as
  the `hassan_key` cookie (set by /login) or `Authorization: Bearer <key>`.
* Pairing is done by scanning a QR code shown on the PC's dashboard.
"""

from __future__ import annotations

import hmac
import secrets
import socket
from pathlib import Path

from fastapi import Request

COOKIE = "hassan_key"


def load_or_create_key(data_dir: Path) -> str:
    path = data_dir / "access_key"
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 32:
            return key
    key = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def rotate_key(data_dir: Path) -> str:
    (data_dir / "access_key").unlink(missing_ok=True)
    return load_or_create_key(data_dir)


def request_host(request: Request) -> str:
    return (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()


PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "tailscale-user-login", "cf-connecting-ip")


def is_local(request: Request, allowed_hosts: list[str], trusted_clients: list[str]) -> bool:
    # Anything that came through a reverse proxy (tailscale serve, a tunnel …) is remote,
    # even though the proxy itself connects from 127.0.0.1 and might rewrite Host.
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    client = request.client.host if request.client else ""
    return client in trusted_clients and request_host(request) in allowed_hosts


def presented_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE)


def key_ok(candidate: str | None, key: str) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate.encode(), key.encode())


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def lan_addresses() -> list[str]:
    """Best-effort list of this PC's LAN IPv4 addresses."""
    addrs: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def qr_svg(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")


LOGIN_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Hassan AI OS</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#e7ecf7;
font-family:"Segoe UI",Tahoma,sans-serif}form{background:#131a2e;border:1px solid #243052;border-radius:14px;
padding:22px;width:min(92vw,380px)}input{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;
border:1px solid #243052;background:#0e1427;color:#e7ecf7;font:inherit;direction:ltr}button{width:100%;
margin-top:12px;padding:12px;border:0;border-radius:8px;background:#4f8cff;color:#fff;font:inherit}
p{color:#8d9bbd;font-size:14px;line-height:1.7}.err{color:#ef5b5b}</style></head><body>
<form method="post" action="/login"><h2>🧠 Hassan AI OS</h2>
<p>امسح رمز الـQR من شاشة الكمبيوتر (كرت 📱 التلفون)، أو الصق مفتاح الدخول هون.</p>__ERR__
<input name="key" placeholder="access key" autocomplete="off"><button>دخول</button></form></body></html>"""
