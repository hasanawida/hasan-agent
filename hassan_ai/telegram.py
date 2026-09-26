"""Telegram bot: talk to Hassan AI OS from Telegram (text or voice), approve steps
with buttons, and receive results, screenshots and renders in the chat.

Security
--------
* Only chats paired with a one-time 6-digit code (shown on the PC dashboard) are
  obeyed. Everyone else gets one polite refusal and is otherwise ignored.
* The bot token lives in hassan.env on the PC; it is never shown back.
* Uses long polling (outgoing HTTPS only): no port is opened on the PC.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .config import save_env_value
from .remote import PairingCodes
from .schemas import Mode, TaskCreate, TaskRecord, TaskStatus

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHATS_ENV = "TELEGRAM_ALLOWED_CHATS"
API = "https://api.telegram.org"
MAX_TEXT = 3900

HELP = """🧠 Hassan AI OS
اكتبلي أو ابعتلي رسالة صوتية، وأنا بشتغل على الكمبيوتر.

الأوامر:
/team <مهمة> — الفريق الكامل (تحليل، برمجة، مراجعة)
/free <مهمة> — مجاني بس (بدون أسئلة) · /best <مهمة> — أقوى عقل مدفوع
(بدون أمر: مجاني، وبسألك بزرّين قبل أي عقل مدفوع)
/status — المهام الشغّالة
/stop — أوقف آخر مهمة
/remember <معلومة> — احفظ معلومة عنك
/schedule daily 08:00 <مهمة> — مهمة مجدولة (daily / weekly sun 09:00 / every 2h)
/schedules — المهام المجدولة
/unschedule <رقم> — احذف مهمة مجدولة
/help — هاي الرسالة"""


class TelegramBot:
    def __init__(self, orch: "Orchestrator", env_file: Path, media_dir: Path, *, api_base: str = API,
                 transport: httpx.AsyncBaseTransport | None = None, scheduler=None, transcriber=None):
        self.orch = orch
        self.env_file = env_file
        self.media_dir = media_dir
        self.api_base = api_base.rstrip("/")
        self.transport = transport
        self.scheduler = scheduler
        self.transcriber = transcriber  # async (bytes, filename) -> str
        self.pairing = PairingCodes()
        self.username: str | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._offset = 0
        self._refused: set[int] = set()
        self._approval_msgs: dict[str, list[tuple[int, int]]] = {}
        self._last_task: dict[int, str] = {}

    # ------------------------------------------------------------ config
    @property
    def token(self) -> str:
        return os.environ.get(TOKEN_ENV, "").strip()

    @property
    def chats(self) -> set[int]:
        raw = os.environ.get(CHATS_ENV, "")
        return {int(c) for c in raw.split(",") if c.strip().lstrip("-").isdigit()}

    def _save_chats(self, chats: set[int]) -> None:
        save_env_value(self.env_file, CHATS_ENV, ",".join(str(c) for c in sorted(chats)))

    def status(self) -> dict:
        return {"configured": bool(self.token), "running": bool(self._task and not self._task.done()),
                "username": self.username, "paired_chats": sorted(self.chats), "error": self.last_error}

    async def set_token(self, token: str) -> dict:
        save_env_value(self.env_file, TOKEN_ENV, token.strip())
        await self.stop()
        self.username = None
        if token.strip():
            await self.start()
        return self.status()

    def unpair(self, chat_id: int) -> None:
        self._save_chats(self.chats - {chat_id})

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if not self.token or (self._task and not self._task.done()):
            return
        self._client = httpx.AsyncClient(timeout=60, transport=self.transport)
        try:
            me = await self._api("getMe")
            self.username = me.get("username")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - report on the dashboard instead of crashing
            self.last_error = f"getMe failed: {exc}"
            await self._client.aclose()
            self._client = None
            return
        if self._listener not in self.orch.listeners:
            self.orch.listeners.append(self._listener)
        self._task = asyncio.get_running_loop().create_task(self._poll())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._listener in self.orch.listeners:
            self.orch.listeners.remove(self._listener)
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------ Bot API
    async def _api(self, method: str, **params) -> Any:
        files = params.pop("_files", None)
        url = f"{self.api_base}/bot{self.token}/{method}"
        if files:
            data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in params.items()}
            resp = await self._client.post(url, data=data, files=files)
        else:
            resp = await self._client.post(url, json=params)
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("description", f"HTTP {resp.status_code}"))
        return body.get("result")

    async def send(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> int | None:
        msg_id = None
        chunks = [text[i:i + MAX_TEXT] for i in range(0, max(len(text), 1), MAX_TEXT)] or [""]
        for i, chunk in enumerate(chunks):
            params: dict = {"chat_id": chat_id, "text": chunk or "…", "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                params["reply_markup"] = {"inline_keyboard": buttons}
            result = await self._api("sendMessage", **params)
            msg_id = result.get("message_id") if isinstance(result, dict) else None
        return msg_id

    async def send_media(self, chat_id: int, name: str, caption: str = "") -> None:
        path = self.media_dir / name
        if not path.is_file():
            return
        method, field = ("sendPhoto", "photo") if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
            else ("sendAudio", "audio") if path.suffix.lower() in (".m4a", ".mp3", ".ogg", ".wav") else ("sendDocument", "document")
        with path.open("rb") as fh:
            await self._api(method, chat_id=chat_id, caption=caption[:900], _files={field: (path.name, fh.read())})

    # ------------------------------------------------------------ polling
    async def _poll(self) -> None:
        while True:
            try:
                updates = await self._api("getUpdates", offset=self._offset, timeout=30,
                                          allowed_updates=["message", "callback_query"])
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network hiccups: retry
                self.last_error = str(exc)[:200]
                await asyncio.sleep(5)
                continue
            for update in updates or []:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                try:
                    await self.handle(update)
                except Exception as exc:  # noqa: BLE001 - one bad update must not stop the bot
                    self.last_error = f"update failed: {exc}"[:200]

    async def handle(self, update: dict) -> None:
        if "callback_query" in update:
            await self._on_button(update["callback_query"])
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/pair"):
            code = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
            if self.pairing.redeem(code):
                self._save_chats(self.chats | {chat_id})
                await self.send(chat_id, "✅ تم ربط هاي المحادثة بـHassan AI OS.\n\n" + HELP)
            else:
                await self.send(chat_id, "❌ الرمز غلط أو انتهى. اطلب رمز جديد من الواجهة على الكمبيوتر.")
            return
        if chat_id not in self.chats:
            if chat_id not in self._refused:
                self._refused.add(chat_id)
                await self.send(chat_id, "🔒 هاد بوت خاص. للربط: من واجهة Hassan على الكمبيوتر اطلب رمز، "
                                         "وابعت هون: /pair 123456")
            return
        if msg.get("voice") or msg.get("audio"):
            text = await self._transcribe(chat_id, msg.get("voice") or msg.get("audio"))
            if not text:
                return
            await self.send(chat_id, f"🎤 سمعتك: {text}")
        if not text:
            return
        await self._command(chat_id, text)

    async def _transcribe(self, chat_id: int, voice: dict) -> str:
        if self.transcriber is None:
            await self.send(chat_id, "🎤 لفهم الرسائل الصوتية ضيف مفتاح Groq المجاني بالواجهة (كرت APIs مجانية).")
            return ""
        info = await self._api("getFile", file_id=voice["file_id"])
        resp = await self._client.get(f"{self.api_base}/file/bot{self.token}/{info['file_path']}")
        try:
            return (await self.transcriber(resp.content, Path(info["file_path"]).name)).strip()
        except Exception as exc:  # noqa: BLE001
            await self.send(chat_id, f"🎤 ما قدرت أفهم الصوت: {exc}")
            return ""

    async def _command(self, chat_id: int, text: str) -> None:
        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower().split("@")[0]
        rest = rest.strip()
        if cmd in ("/start", "/help"):
            await self.send(chat_id, HELP)
        elif cmd == "/status":
            active = [t for t in self.orch.memory.list_tasks(30)
                      if t.status in (TaskStatus.running, TaskStatus.awaiting_approval, TaskStatus.queued)]
            await self.send(chat_id, "\n".join(f"• {t.status.value}: {t.prompt[:80]}" for t in active) or "ما في مهام شغّالة.")
        elif cmd == "/stop":
            task_id = self._last_task.get(chat_id)
            if task_id:
                self.orch.cancel(task_id)
                await self.send(chat_id, "⏹ وقفت آخر مهمة.")
            else:
                await self.send(chat_id, "ما في مهمة أوقفها.")
        elif cmd == "/remember":
            if rest:
                self.orch.memory.remember("_hassan", "profile", rest)
                await self.send(chat_id, "🧠 حفظتها.")
        elif cmd == "/schedule" and self.scheduler is not None:
            try:
                sched = self.scheduler.add_from_text(rest, origin=f"telegram:{chat_id}")
                await self.send(chat_id, f"⏰ تمام: {sched['spec']} ← {sched['prompt'][:80]}\nالتشغيل الجاي: {sched['next_human']}")
            except ValueError as exc:
                await self.send(chat_id, f"❌ {exc}\nمثال: /schedule daily 08:00 ابعتلي ملخص إيميلاتي")
        elif cmd == "/schedules" and self.scheduler is not None:
            items = self.scheduler.list()
            await self.send(chat_id, "\n".join(f"{s['id']}. {s['spec']} — {s['prompt'][:60]}" for s in items) or "ما في مهام مجدولة.")
        elif cmd == "/unschedule" and self.scheduler is not None:
            ok = self.scheduler.remove(rest)
            await self.send(chat_id, "🗑 انحذفت." if ok else "ما لقيتها.")
        elif cmd == "/team":
            await self._start_task(chat_id, rest, "project")
        elif cmd in ("/free", "/best"):
            await self._start_task(chat_id, rest, "operate", "free" if cmd == "/free" else "best")
        else:
            await self._start_task(chat_id, text, "operate")

    async def _start_task(self, chat_id: int, prompt: str, kind: str, budget: str = "auto") -> None:
        if not prompt:
            await self.send(chat_id, "اكتب المهمة بعد الأمر.")
            return
        task = self.orch.submit(TaskCreate(prompt=prompt, kind=kind, mode=Mode.auto, budget=budget,
                                           origin=f"telegram:{chat_id}"))
        self._last_task[chat_id] = task.id
        await self.send(chat_id, "⏳ بلّشت" + (" (الفريق)" if kind == "project" else "") + "…")

    # ------------------------------------------------------------ approvals
    async def _on_button(self, cq: dict) -> None:
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        data = str(cq.get("data") or "")
        if chat_id not in self.chats:
            await self._api("answerCallbackQuery", callback_query_id=cq["id"], text="غير مسموح")
            return
        verb, _, approval_id = data.partition(":")
        answer = "تم"
        try:
            await self.orch.decide_approval(approval_id, verb in ("ok", "all"), trust_similar=verb == "all")
            answer = {"ok": "✅ موافق", "all": "✅✅ موافق على المشابه", "no": "❌ رفضت"}.get(verb, "تم")
        except (KeyError, ValueError) as exc:
            answer = f"ما صار: {exc}"[:180]
        await self._api("answerCallbackQuery", callback_query_id=cq["id"], text=answer)
        for c, m in self._approval_msgs.pop(approval_id, []):
            try:
                await self._api("editMessageReplyMarkup", chat_id=c, message_id=m, reply_markup={"inline_keyboard": []})
                await self.send(c, answer)
            except Exception:  # noqa: BLE001
                pass

    def _targets(self, task: TaskRecord) -> list[int]:
        paired = self.chats
        if task.origin and task.origin.startswith("telegram:"):
            cid = int(task.origin.split(":", 1)[1])
            return [cid] if cid in paired else []
        return sorted(paired)

    def _listener(self, task: TaskRecord, kind: str, message: str, data: dict) -> None:
        if kind == "approval_required":
            asyncio.get_running_loop().create_task(self._notify_approval(task, data.get("approval_id", "")))
        elif kind == "done" and task.origin and (task.origin.startswith("telegram:") or task.origin.startswith("schedule:")):
            asyncio.get_running_loop().create_task(self._notify_done(task.id))
        elif kind == "error" and task.origin and task.origin.startswith("telegram:"):
            asyncio.get_running_loop().create_task(self._send_all(self._targets(task), f"⚠️ صار خطأ: {message[:500]}"))

    async def _send_all(self, chats: list[int], text: str) -> None:
        for c in chats:
            try:
                await self.send(c, text)
            except Exception:  # noqa: BLE001
                pass

    async def _notify_approval(self, task: TaskRecord, approval_id: str) -> None:
        approval = self.orch.memory.get_approval(approval_id)
        if approval is None:
            return
        buttons = [[{"text": "✅ موافق", "callback_data": f"ok:{approval.id}"},
                    {"text": "❌ رفض", "callback_data": f"no:{approval.id}"}]]
        if approval.payload.get("trustable"):
            buttons.append([{"text": "✅✅ وافق على كل المشابه بهاي المهمة", "callback_data": f"all:{approval.id}"}])
        detail = (approval.diff or "")[:1500]
        text = f"⚠️ موافقة مطلوبة\n{approval.title}\n\n{detail}".strip()
        for c in self._targets(task):
            try:
                mid = await self.send(c, text, buttons)
                if mid:
                    self._approval_msgs.setdefault(approval.id, []).append((c, mid))
            except Exception:  # noqa: BLE001
                pass

    async def _notify_done(self, task_id: str) -> None:
        task = self.orch.memory.get_task(task_id)
        if task is None:
            return
        chats = self._targets(task) if task.origin.startswith("telegram:") else sorted(self.chats)
        head = "✅ خلصت" if task.status == TaskStatus.completed else f"ℹ️ {task.status.value}"
        prefix = "⏰ مهمة مجدولة: " + task.prompt[:80] + "\n" if task.origin.startswith("schedule:") else ""
        for c in chats:
            try:
                await self.send(c, f"{prefix}{head}\n\n{task.decision or ''}".strip())
                for ev in [e for e in task.evidence if e.media][-4:]:
                    await self.send_media(c, ev.media, ev.title)
            except Exception:  # noqa: BLE001
                pass


async def groq_transcriber(audio: bytes, filename: str) -> str:
    """Speech-to-text with Groq's free Whisper endpoint (needs GROQ_API_KEY)."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ضيف مفتاح Groq المجاني بالواجهة (كرت APIs مجانية) عشان أفهم الرسائل الصوتية")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 data={"model": "whisper-large-v3-turbo", "response_format": "json"},
                                 files={"file": (filename or "voice.ogg", audio)})
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("text", "")
