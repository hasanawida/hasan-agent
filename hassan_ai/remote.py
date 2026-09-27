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


def qr_html(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    # A crisp PNG at a whole-pixel scale (vector strokes leave hairline seams that cameras
    # misread), with the 4-module quiet zone the QR spec requires.
    uri = segno.make(text, error="m").png_data_uri(scale=6, border=4, dark="#000", light="#fff")
    return f'<img src="{uri}" alt="QR" width="100%" style="image-rendering:pixelated;display:block">'


class PairingCodes:
    """Short one-time codes as a fallback when the camera can't scan the QR.
    6 digits, valid 10 minutes, single use, locked after 5 wrong tries."""

    TTL = 600
    MAX_TRIES = 5
    TICKET_TTL = 300

    def __init__(self) -> None:
        self.code: str | None = None
        self.expires = 0.0
        self.tries = 0
        self.ticket: str | None = None
        self.ticket_expires = 0.0
        import threading
        self._ticket_lock = threading.Lock()

    def new(self) -> dict:
        import time

        self.code = f"{secrets.randbelow(10**6):06d}"
        self.expires = time.time() + self.TTL
        self.tries = 0
        return {"code": self.code, "expires_in": self.TTL}

    def redeem(self, candidate: str) -> bool:
        import time

        if not self.code or time.time() > self.expires:
            self.code = None
            return False
        if hmac.compare_digest(candidate.strip().encode(), self.code.encode()):
            self.code = None
            return True
        self.tries += 1
        if self.tries >= self.MAX_TRIES:
            self.code = None
        return False


    def new_ticket(self) -> dict:
        """Create an opaque one-use QR credential, independent of the typed code.

        Only the newest QR ticket is valid. Ticket state is memory-only and dies
        on server restart; no master credential belongs in a QR URL.
        """
        import time

        with self._ticket_lock:
            self.ticket = "qr_" + secrets.token_urlsafe(32)
            self.ticket_expires = time.time() + self.TICKET_TTL
            return {"ticket": self.ticket, "expires_in": self.TICKET_TTL}

    def redeem_ticket(self, candidate: str | None) -> bool:
        """Consume one QR ticket without touching the numeric fallback code."""
        import time

        if not isinstance(candidate, str) or not 40 <= len(candidate) <= 128 or not candidate.startswith("qr_"):
            return False
        with self._ticket_lock:
            if not self.ticket or time.time() >= self.ticket_expires:
                self.ticket = None
                return False
            if not hmac.compare_digest(candidate.encode(), self.ticket.encode()):
                return False
            self.ticket = None
            self.ticket_expires = 0.0
            return True


LOGIN_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Hassan AI OS</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#e7ecf7;
font-family:"Segoe UI",Tahoma,sans-serif}form{background:#131a2e;border:1px solid #243052;border-radius:14px;
padding:22px;width:min(92vw,380px)}input{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;
border:1px solid #243052;background:#0e1427;color:#e7ecf7;font:inherit;direction:ltr}button{width:100%;
margin-top:12px;padding:12px;border:0;border-radius:8px;background:#4f8cff;color:#fff;font:inherit}
p{color:#8d9bbd;font-size:14px;line-height:1.7}.err{color:#ef5b5b}</style></head><body>
<form method="post" action="/login"><h2>🧠 Hassan AI OS</h2>
<p>امسح رمز الـQR من شاشة الكمبيوتر (كرت 📱 التلفون)،<br>أو اكتب <b>الرمز من 6 أرقام</b> اللي بيظهر هناك.</p>__ERR__
<input name="key" placeholder="123456" inputmode="numeric" autocomplete="one-time-code" style="font-size:26px;
letter-spacing:6px;text-align:center"><button>دخول</button></form></body></html>"""
