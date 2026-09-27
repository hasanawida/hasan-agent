"""Time-limited manual desktop control for Hassan's authenticated dashboard.

Screen viewing stays on the existing FFmpeg stream. Input is opt-in on the PC,
belongs to one WebSocket, and is released on disconnect, expiry or revocation.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import math
import os
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect

from . import remote

KEYS = {
    "Enter": "enter", "Backspace": "backspace", "Tab": "tab", "Escape": "esc",
    "Delete": "delete", "Insert": "insert", "Home": "home", "End": "end",
    "PageUp": "page_up", "PageDown": "page_down", "ArrowUp": "up", "ArrowDown": "down",
    "ArrowLeft": "left", "ArrowRight": "right", "Shift": "shift", "Control": "ctrl",
    "Alt": "alt", "Meta": "cmd", "CapsLock": "caps_lock",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}

# Named shortcuts also work from a phone, where the browser/OS reserves keys.
# Virtual letter keys keep Ctrl+C, Win+E, etc. working with Arabic layouts.
SHORTCUTS = {
    "start": ("Meta",), "files": ("Meta", "KeyE"),
    "desktop": ("Meta", "KeyD"), "switch": ("Alt", "Tab"),
    "run": ("Meta", "KeyR"), "task-manager": ("Control", "Shift", "Escape"),
    "select-all": ("Control", "KeyA"), "copy": ("Control", "KeyC"),
    "paste": ("Control", "KeyV"), "cut": ("Control", "KeyX"),
    "undo": ("Control", "KeyZ"), "redo": ("Control", "KeyY"),
}


def valid_input(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    action = data.get("action")
    if action in ("release", "heartbeat"):
        return True
    if action == "move":
        return all(type(data.get(k)) in (int, float) and 0 <= data[k] <= 1 and math.isfinite(data[k])
                   for k in ("x", "y"))
    if action == "button":
        return data.get("button") in ("left", "middle", "right") and type(data.get("down")) is bool
    if action == "scroll":
        return type(data.get("dy")) is int and -5 <= data["dy"] <= 5
    if action == "shortcut":
        return isinstance(data.get("name"), str) and data["name"] in SHORTCUTS
    if action == "key":
        key = data.get("key")
        return (isinstance(key, str) and (key in KEYS or (len(key) == 1 and key.isprintable()))
                and type(data.get("down")) is bool)
    if action == "text":
        value = data.get("text")
        return isinstance(value, str) and 0 < len(value) <= 256 and all(c.isprintable() or c in "\n\t" for c in value)
    return False


class InputState:
    """Device-independent input bookkeeping, also exercised with fake devices."""
    def __init__(self, mouse, keyboard, buttons, keys, bounds):
        self.mouse, self.keyboard = mouse, keyboard
        self.buttons, self.keys, self.bounds = buttons, keys, bounds
        self.held_keys: dict = {}
        self.held_buttons: set = set()

    def apply(self, data):
        if not valid_input(data):
            raise ValueError("Invalid input")
        action = data["action"]
        if action == "release":
            self.release()
        elif action == "move":
            left, top, width, height = self.bounds()
            self.mouse.position = (left + round(data["x"] * (width - 1)), top + round(data["y"] * (height - 1)))
        elif action == "button":
            button = self.buttons[data["button"]]
            if data["down"]:
                self.held_buttons.add(button)
                self.mouse.press(button)
            elif button in self.held_buttons:
                self.mouse.release(button)
                self.held_buttons.discard(button)
        elif action == "scroll":
            self.mouse.scroll(0, data["dy"])
        elif action == "text":
            self.release()
            self.keyboard.type(data["text"])
        elif action == "shortcut":
            self.release()
            try:
                for name in SHORTCUTS[data["name"]]:
                    key = self.keys[name]
                    self.held_keys[name] = key
                    self.keyboard.press(key)
            finally:
                self.release()
        elif action == "key":
            name = data["key"]
            if data["down"]:
                if len(self.held_keys) >= 32 and name not in self.held_keys:
                    raise ValueError("Too many pressed keys")
                key = self.keys.get(name, name)
                self.held_keys[name] = key
                self.keyboard.press(key)
            elif name in self.held_keys:
                self.keyboard.release(self.held_keys[name])
                del self.held_keys[name]

    def release(self):
        for key in reversed(list(self.held_keys.values())):
            try:
                self.keyboard.release(key)
            except Exception:
                pass
        for button in list(self.held_buttons):
            try:
                self.mouse.release(button)
            except Exception:
                pass
        self.held_keys.clear()
        self.held_buttons.clear()


def windows_input() -> InputState:
    # All creation, input and cleanup run on ONE worker. DPI awareness belongs
    # to that thread; no process-wide change to Hassan or its other tools.
    import ctypes
    from pynput import keyboard, mouse

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if hasattr(user32, "SetThreadDpiAwarenessContext"):
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    return InputState(mouse.Controller(), keyboard.Controller(),
                      {name: getattr(mouse.Button, name) for name in ("left", "middle", "right")},
                      {**{name: getattr(keyboard.Key, key) for name, key in KEYS.items()},
                       **{f"Key{letter}": keyboard.KeyCode.from_vk(ord(letter)) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}},
                      lambda: tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)))


class DesktopControl:
    GRANT_SECONDS = 30 * 60
    HEARTBEAT_SECONDS = 10
    RELEASE_SECONDS = 3

    def __init__(self):
        self.supported = os.name == "nt" and importlib.util.find_spec("pynput") is not None
        self.backend_factory = windows_input
        self.backend = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hassan-desktop")
        self.lock = asyncio.Lock()
        self.expires = 0.0
        self.owner = None
        self.last_seen = self.last_input = 0.0
        self.watchdog = None

    async def work(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)

    def status(self):
        seconds = max(0, math.ceil(self.expires - time.monotonic()))
        return {"supported": self.supported, "enabled": seconds > 0, "expires_in": seconds,
                "connected": self.owner is not None}

    async def enable(self):
        async with self.lock:
            if not self.supported:
                raise HTTPException(503, "التحكّم اليدوي يحتاج Windows ومكتبة pynput. حدّث التثبيت ثم أعد تشغيل Hassan.")
            if self.backend is None:
                try:
                    self.backend = await self.work(self.backend_factory)
                except Exception as exc:
                    raise HTTPException(503, "تعذّر تهيئة التحكّم على ويندوز.") from exc
            self.expires = time.monotonic() + self.GRANT_SECONDS
            return self.status()

    async def disable(self):
        async with self.lock:
            self.expires = 0
            owner, self.owner = self.owner, None
            if self.backend:
                await self.work(self.backend.release)
        if owner:
            try:
                await asyncio.wait_for(owner.close(code=4001, reason="Control disabled"), 2)
            except (RuntimeError, asyncio.TimeoutError, OSError):
                pass

    async def connect(self, socket):
        async with self.lock:
            if self.expires <= time.monotonic() or self.backend is None:
                return False
            if self.owner is not None:
                return False
            self.owner = socket
            self.last_seen = self.last_input = time.monotonic()
            return True

    async def disconnect(self, socket):
        async with self.lock:
            if self.owner is socket:
                self.owner = None
                if self.backend:
                    await self.work(self.backend.release)

    async def input(self, socket, data):
        async with self.lock:
            if self.owner is not socket or self.expires <= time.monotonic():
                raise ValueError("Control is no longer enabled")
            self.last_seen = time.monotonic()
            if data["action"] != "heartbeat":
                await self.work(self.backend.apply, data)
                self.last_input = time.monotonic()

    async def sweep(self):
        while True:
            await asyncio.sleep(.25)
            now = time.monotonic()
            if self.expires and now >= self.expires:
                await self.disable()
                continue
            stale = None
            async with self.lock:
                if self.owner and now - self.last_seen > self.HEARTBEAT_SECONDS:
                    stale, self.owner = self.owner, None
                    await self.work(self.backend.release)
                elif self.owner and now - self.last_input > self.RELEASE_SECONDS:
                    await self.work(self.backend.release)
                    self.last_input = now
            if stale:
                try:
                    await asyncio.wait_for(stale.close(code=4002, reason="Connection timed out"), 2)
                except (RuntimeError, asyncio.TimeoutError, OSError):
                    pass

    def start(self):
        self.watchdog = asyncio.create_task(self.sweep())

    async def close(self):
        if self.watchdog:
            self.watchdog.cancel()
            await asyncio.gather(self.watchdog, return_exceptions=True)
        await self.disable()
        self.executor.shutdown(wait=True)


def same_origin(connection) -> bool:
    """Exact origin, including port; no permissive localhost-hostname exception."""
    value = connection.headers.get("origin", "")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    scheme = "https" if (connection.url.scheme in ("https", "wss") or
                           connection.headers.get("x-forwarded-proto", "").lower() == "https") else "http"
    return (parsed.scheme == scheme and parsed.netloc.lower() == connection.headers.get("host", "").lower()
            and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)


def attach_routes(app, settings, desktop):
    @app.get("/api/desktop")
    async def status(request: Request):
        return {**desktop.status(), "local": request.state.local}

    @app.post("/api/desktop/enable")
    async def enable(request: Request):
        if not request.state.local or not same_origin(request):
            raise HTTPException(403, "فعّل التحكّم من صفحة Hassan على الكمبيوتر نفسه.")
        return await desktop.enable()

    @app.post("/api/desktop/disable")
    async def disable(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "Same-origin request required")
        await desktop.disable()
        return {"ok": True}

    @app.websocket("/api/desktop/control")
    async def control(socket: WebSocket):
        # HTTP middleware does NOT run on WebSockets. Repeat authentication and
        # origin checks here, and never put the long-lived key in a URL.
        local = remote.is_local(socket, settings.allowed_hosts, settings.trusted_clients)
        key = remote.presented_key(socket)
        secure = socket.url.scheme == "wss" or socket.headers.get("x-forwarded-proto", "").lower() == "https"
        if not same_origin(socket) or (not local and (not secure or not remote.key_ok(key, app.state.access_key))):
            await socket.close(code=1008)
            return
        await socket.accept()
        if not await desktop.connect(socket):
            await socket.send_json({"type": "error", "message": "فعّل التحكّم من الكمبيوتر أولًا، أو أنهِ جلسة التحكّم المفتوحة."})
            await socket.close(code=4003)
            return
        try:
            await socket.send_json({"type": "ready"})
            started, count = time.monotonic(), 0
            while True:
                raw = await socket.receive_text()
                if len(raw.encode("utf-8")) > 4096:
                    raise ValueError("Input too large")
                if not local and not remote.key_ok(key, app.state.access_key):
                    break
                now = time.monotonic()
                if now - started >= 1:
                    started, count = now, 0
                count += 1
                if count > 180:
                    raise ValueError("Too many input messages")
                data = json.loads(raw)
                if not valid_input(data):
                    raise ValueError("Invalid input")
                await desktop.input(socket, data)
                if data["action"] == "heartbeat":
                    await socket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            try:
                await socket.close(code=1008)
            except (RuntimeError, OSError):
                pass
        finally:
            await desktop.disconnect(socket)
