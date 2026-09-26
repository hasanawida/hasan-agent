"""Scheduled tasks ("every day at 08:00 send me…"), in the spirit of Hermes' cron.

Specs (local time):
    daily 08:00
    weekly sun 09:30        (sun mon tue wed thu fri sat, or Arabic day names)
    every 30m | every 2h    (minimum 15 minutes)
Results go to Telegram when a chat is paired, and are always visible on the dashboard.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .schemas import Mode, TaskCreate

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "الاثنين": 0, "الإثنين": 0, "الثلاثاء": 1, "الاربعاء": 2, "الأربعاء": 2, "الخميس": 3,
        "الجمعة": 4, "السبت": 5, "الاحد": 6, "الأحد": 6}
MIN_INTERVAL = 15 * 60


def parse_spec(text: str) -> tuple[str, str]:
    """Split 'daily 08:00 do X' into (spec, prompt). Raises ValueError on bad input."""
    t = text.strip()
    m = re.match(r"(?i)^(daily|يوميا|يومياً)\s+(\d{1,2}):(\d{2})\s+(.+)$", t, re.S)
    if m:
        h, mi = int(m.group(2)), int(m.group(3))
        _check_time(h, mi)
        return f"daily {h:02d}:{mi:02d}", m.group(4).strip()
    m = re.match(r"(?i)^(weekly|اسبوعيا|أسبوعياً|اسبوعياً)\s+(\S+)\s+(\d{1,2}):(\d{2})\s+(.+)$", t, re.S)
    if m:
        day = m.group(2).lower()[:3] if m.group(2).isascii() else m.group(2)
        if day not in DAYS:
            raise ValueError(f"يوم مش معروف: {m.group(2)}")
        h, mi = int(m.group(3)), int(m.group(4))
        _check_time(h, mi)
        return f"weekly {day} {h:02d}:{mi:02d}", m.group(5).strip()
    m = re.match(r"(?i)^(every|كل)\s+(\d+)\s*(m|min|h|hour|د|دقيقة|س|ساعة)\w*\s+(.+)$", t, re.S)
    if m:
        n = int(m.group(2))
        seconds = n * (3600 if m.group(3).lower() in ("h", "hour", "س", "ساعة") else 60)
        if seconds < MIN_INTERVAL:
            raise ValueError("أقل مدة 15 دقيقة")
        return f"every {seconds}s", m.group(4).strip()
    raise ValueError("الصيغة: daily 08:00 <مهمة> | weekly sun 09:00 <مهمة> | every 2h <مهمة>")


def _check_time(h: int, mi: int) -> None:
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError("وقت غلط")


def next_run(spec: str, after: float) -> float:
    base = datetime.fromtimestamp(after)
    parts = spec.split()
    if parts[0] == "every":
        return after + int(parts[1].rstrip("s"))
    h, mi = map(int, parts[-1].split(":"))
    cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if parts[0] == "daily":
        if cand <= base:
            cand += timedelta(days=1)
        return cand.timestamp()
    target = DAYS[parts[1]]
    cand += timedelta(days=(target - cand.weekday()) % 7)
    if cand <= base:
        cand += timedelta(days=7)
    return cand.timestamp()


def human(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class Scheduler:
    def __init__(self, orch: "Orchestrator", tick: float = 20.0):
        self.orch = orch
        self.tick = tick
        self._task: asyncio.Task | None = None

    def add_from_text(self, text: str, origin: str | None = None, kind: str = "operate") -> dict:
        spec, prompt = parse_spec(text)
        if prompt.lower().startswith(("/team ", "team:")):
            kind, prompt = "project", prompt.split(None, 1)[1] if " " in prompt else ""
        nxt = next_run(spec, time.time())
        sid = self.orch.memory.add_schedule(spec, prompt, kind, origin, nxt)
        return {"id": sid, "spec": spec, "prompt": prompt, "kind": kind, "next_run": nxt, "next_human": human(nxt)}

    def list(self) -> list[dict]:
        return [{**s, "next_human": human(s["next_run"]),
                 "last_human": human(s["last_run"]) if s.get("last_run") else None}
                for s in self.orch.memory.schedules()]

    def remove(self, schedule_id) -> bool:
        try:
            return self.orch.memory.delete_schedule(int(str(schedule_id).strip()))
        except ValueError:
            return False

    def run_due(self, at: float | None = None) -> list[str]:
        at = at or time.time()
        started = []
        for s in self.orch.memory.schedules():
            if s["next_run"] > at:
                continue
            task = self.orch.submit(TaskCreate(prompt=s["prompt"], kind=s["kind"], mode=Mode.auto,
                                               origin=f"schedule:{s['id']}"))
            # never "catch up" a backlog after the PC was off: schedule from now
            self.orch.memory.update_schedule_run(s["id"], at, next_run(s["spec"], at))
            started.append(task.id)
        return started

    async def _loop(self) -> None:
        while True:
            try:
                self.run_due()
            except Exception:  # noqa: BLE001 - keep the clock running
                pass
            await asyncio.sleep(self.tick)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
