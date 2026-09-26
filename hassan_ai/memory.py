"""SQLite persistence: tasks, events, approvals, project memory and model stats."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .schemas import Approval, Event, TaskRecord, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id);
CREATE TABLE IF NOT EXISTS project_memory (
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_project ON project_memory(project, kind);
CREATE TABLE IF NOT EXISTS usage (
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL,
    duration REAL NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_task ON usage(task_id);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT,
    next_run REAL NOT NULL,
    last_run REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_stats (
    model TEXT NOT NULL,
    agent TEXT NOT NULL,
    ok INTEGER NOT NULL,
    duration REAL NOT NULL,
    ts REAL NOT NULL
);
"""


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # --- tasks -------------------------------------------------------------
    def save_task(self, task: TaskRecord) -> None:
        task.updated_at = now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id,status,created_at,updated_at,body) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "updated_at=excluded.updated_at, body=excluded.body",
                (task.id, task.status.value, task.created_at, task.updated_at, task.model_dump_json()),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
        return TaskRecord.model_validate_json(row["body"]) if row else None

    def list_tasks(self, limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [TaskRecord.model_validate_json(r["body"]) for r in rows]

    # --- events ------------------------------------------------------------
    def add_event(self, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(task_id,kind,message,data,ts) VALUES(?,?,?,?,?)",
                (event.task_id, event.kind, event.message, json.dumps(event.data, ensure_ascii=False), event.ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq,task_id,kind,message,data,ts FROM events WHERE task_id=? AND seq>? ORDER BY seq",
                (task_id, after),
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # --- approvals ---------------------------------------------------------
    def save_approval(self, approval: Approval) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals(id,task_id,status,body) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                (approval.id, approval.task_id, approval.status, approval.model_dump_json()),
            )
            self._conn.commit()

    def get_approval(self, approval_id: str) -> Approval | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return Approval.model_validate_json(row["body"]) if row else None

    def approvals(self, task_id: str | None = None, status: str | None = None) -> list[Approval]:
        sql, args = "SELECT body FROM approvals WHERE 1=1", []
        if task_id:
            sql += " AND task_id=?"
            args.append(task_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Approval.model_validate_json(r["body"]) for r in rows]

    # --- project memory ----------------------------------------------------
    def remember(self, project: str, kind: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO project_memory(project,kind,content,ts) VALUES(?,?,?,?)",
                (project, kind, content, now()),
            )
            self._conn.commit()

    def recall(self, project: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind,content,ts FROM project_memory WHERE project=? ORDER BY ts DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, project: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM project_memory WHERE project=? AND content=?", (project, content))
            self._conn.commit()
            return cur.rowcount

    def search_tasks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM tasks WHERE body LIKE ? ORDER BY created_at DESC LIMIT ?", (like, limit)).fetchall()
        out = []
        for r in rows:
            t = TaskRecord.model_validate_json(r["body"])
            out.append({"id": t.id, "when": t.created_at, "prompt": t.prompt[:300], "status": t.status.value,
                        "result": (t.decision or "")[:600]})
        return out

    # --- schedules ----------------------------------------------------------
    def add_schedule(self, spec: str, prompt: str, kind: str, origin: str | None, next_run: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO schedules(spec,prompt,kind,origin,next_run,created_at) VALUES(?,?,?,?,?,?)",
                (spec, prompt, kind, origin, next_run, now()))
            self._conn.commit()
            return int(cur.lastrowid)

    def schedules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM schedules ORDER BY next_run").fetchall()]

    def update_schedule_run(self, schedule_id: int, last_run: float, next_run: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE schedules SET last_run=?, next_run=? WHERE id=?", (last_run, next_run, schedule_id))
            self._conn.commit()

    def delete_schedule(self, schedule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- capability registry (measured, not guessed) ----------------------
    def record_model_call(self, model: str, agent: str, ok: bool, duration: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO model_stats(model,agent,ok,duration,ts) VALUES(?,?,?,?,?)",
                (model, agent, int(ok), duration, now()),
            )
            self._conn.commit()

    # --- usage / spend ------------------------------------------------------
    def record_usage(self, task_id: str, agent: str, model: str, input_tokens: int, output_tokens: int,
                     cost_usd: float | None, duration: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage(task_id,agent,model,input_tokens,output_tokens,cost_usd,duration,ts) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, agent, model, input_tokens, output_tokens, cost_usd, duration, now()),
            )
            self._conn.commit()

    def usage_summary(self, since: float) -> dict[str, Any]:
        sql_total = ("SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens, "
                     "COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd, "
                     "COALESCE(SUM(duration),0) AS seconds FROM usage WHERE ts>=?")
        sql_model = ("SELECT model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
                     "SUM(output_tokens) AS output_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd, "
                     "SUM(duration) AS seconds FROM usage WHERE ts>=? GROUP BY model ORDER BY calls DESC")
        with self._lock:
            total = dict(self._conn.execute(sql_total, (since,)).fetchone())
            by_model = [dict(r) for r in self._conn.execute(sql_model, (since,)).fetchall()]
            tasks = self._conn.execute("SELECT COUNT(DISTINCT task_id) FROM usage WHERE ts>=?", (since,)).fetchone()[0]
        return {**total, "tasks": tasks, "by_model": by_model}

    def task_usage(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd, "
                "COALESCE(SUM(duration),0) AS seconds FROM usage WHERE task_id=?", (task_id,)).fetchone()
        return dict(row)

    def model_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT model, agent, COUNT(*) AS calls, AVG(ok) AS success_rate, "
                "AVG(duration) AS avg_seconds FROM model_stats GROUP BY model, agent ORDER BY model, agent"
            ).fetchall()
        return [dict(r) for r in rows]
