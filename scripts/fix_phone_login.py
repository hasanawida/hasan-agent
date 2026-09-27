"""One-time fix for copies of Hassan AI OS on another branch: phone login through Tailscale.

tailscale serve forwards the phone's request with Host rewritten to 127.0.0.1:8787 and the
real https://<pc>.ts.net name in X-Forwarded-Host. The dashboard's cross-site check compared
the browser's Origin with Host only, so the phone's login form and buttons were rejected
with "Cross-site request blocked". This script:

  1. lets the dashboard's own requests through when their Origin matches the forwarded
     Tailscale host (other sites are still blocked), and never blocks the /login form
     (it only works with the secret code anyway);
  2. shows one QR code on the phone card (Tailscale) and folds the Wi-Fi-only ones away.

Run from the project folder:   .venv\\Scripts\\python.exe scripts\\fix_phone_login.py
It is safe to run twice: files that are already fixed are left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "hassan_ai" / "server.py"
INDEX = ROOT / "hassan_ai" / "static" / "index.html"

GUARD_OLD = ('        if origin and request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):\n')
GUARD_NEW = (
    '        if (origin and request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/login"\n'
    '                and not same_origin(request) and not tailscale_origin(request)):\n'
)
HELPER_ANCHOR = "def create_app("
HELPER = '''def tailscale_origin(request) -> bool:
    """The page's own origin when it came through `tailscale serve`, which rewrites Host to
    127.0.0.1:8787 and reports the phone's https://<pc>.ts.net name in X-Forwarded-Host.
    A form or script on another site cannot set that header, so this stays safe."""
    from urllib.parse import urlsplit as _split

    forwarded = request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
    if not forwarded:
        return False
    try:
        origin = _split(request.headers.get("origin", ""))
    except ValueError:
        return False
    return origin.scheme == "https" and origin.netloc.lower() in (forwarded, forwarded.rsplit(":", 1)[0])


'''

QR_OLD = '''  $("phone").innerHTML = r.urls.map(u => `
    <p><b>${u.kind === "tailscale" ? "من أي مكان (Tailscale)" : "نفس الـWi-Fi فقط"}</b></p>
    <p>افتح كاميرا التلفون وامسح الرمز:</p>
    <div class="qr">${u.qr_html}</div>
    <p dir="ltr" style="font-size:11px;word-break:break-all">${esc(u.base)}</p>
    ${u.kind === "wifi" ? "<p>⚠️ بدون HTTPS: المايكروفون ما بيشتغل على التلفون. استخدم Tailscale.</p>" : ""}`).join("") +'''
QR_NEW = '''  // One QR: Tailscale works from anywhere with HTTPS; the Wi-Fi addresses are only a fallback.
  const qrBlock = u => `
    <p><b>${u.kind === "tailscale" ? "من أي مكان (Tailscale)" : "نفس الـWi-Fi فقط"}</b></p>
    <p>افتح كاميرا التلفون وامسح الرمز:</p>
    <div class="qr">${u.qr_html}</div>
    <p dir="ltr" style="font-size:11px;word-break:break-all">${esc(u.base)}</p>
    ${u.kind === "wifi" ? "<p>⚠️ بدون HTTPS: المايكروفون ما بيشتغل على التلفون. استخدم Tailscale.</p>" : ""}`;
  const main = r.urls.find(u => u.kind === "tailscale") || r.urls[0];
  const others = r.urls.filter(u => u !== main);
  $("phone").innerHTML = qrBlock(main) +
    (others.length ? `<details style="margin:8px 0"><summary>روابط تانية (نفس الـWi-Fi بس)</summary>${others.map(qrBlock).join("")}</details>` : "") +'''
PAIR_OLD = '<span dir="ltr">${esc(r.urls[0].base)}</span>'
PAIR_NEW = '<span dir="ltr">${esc(main.base)}</span>'


def patch(path: Path, steps: list[tuple[str, str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    changed = False
    for name, old, new in steps:
        if new in text:
            print(f"  ✓ {name}: already fixed")
        elif old in text:
            text = text.replace(old, new, 1)
            changed = True
            print(f"  ✓ {name}: fixed")
        else:
            print(f"  ✗ {name}: this copy looks different — nothing changed here")
    if changed:
        path.write_text(text, encoding="utf-8", newline="")


def main() -> int:
    for f in (SERVER, INDEX):
        if not f.exists():
            print(f"Not found: {f}. Run this from the Hassan AI OS folder.")
            return 1
    print("server.py")
    text = SERVER.read_text(encoding="utf-8")
    if "def tailscale_origin(" not in text and HELPER_ANCHOR in text:
        SERVER.write_text(text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1), encoding="utf-8", newline="")
        print("  ✓ tailscale check: added")
    patch(SERVER, [("login and phone buttons through Tailscale", GUARD_OLD, GUARD_NEW)])
    print("index.html")
    patch(INDEX, [("one QR code", QR_OLD, QR_NEW), ("6-digit code link", PAIR_OLD, PAIR_NEW)])
    print("\nDone. Restart Hassan:  .venv\\Scripts\\python.exe -m hassan_ai stop  then  "
          ".venv\\Scripts\\python.exe -m hassan_ai open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
