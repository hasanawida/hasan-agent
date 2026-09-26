"""Hassan Orchestrator: Manager → Analyst → Planner → (Researcher ∥ Coder) →
Reviewer → Judge → [approval → apply → verify → repair] → Decision.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pydantic import ValidationError

from .agents import Roster
from .config import Settings
from .execution import ExecutionManager, ProjectInfo
from .llm import LLM, LLMError, extract_json
from .memory import Memory
from .policy import PolicyError, resolve_workspace
from .schemas import (AgentOutput, Approval, ChangePlan, Event, Evidence, Mode, TaskCreate,
                      TaskRecord, TaskStatus, now)

MAX_FILE_CONTEXT = 40_000


class Orchestrator:
    def __init__(self, settings: Settings, memory: Memory, llm: LLM, roster: Roster, execution: ExecutionManager):
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.roster = roster
        self.execution = execution
        self._running: set[asyncio.Task] = set()
        self._approval_lock = asyncio.Lock()

    # ------------------------------------------------------------------ API
    def submit(self, req: TaskCreate) -> TaskRecord:
        task = TaskRecord(prompt=req.prompt, mode=req.mode, workspace=req.workspace,
                          execute=req.execute, project=req.project)
        self.memory.save_task(task)
        self._emit(task, "created", "Task received", {"mode": task.mode.value, "execute": task.execute})
        self._spawn(self.run(task.id))
        return task

    def recover_interrupted(self) -> int:
        """Tasks cut off by a restart are marked failed (awaiting-approval tasks survive)."""
        count = 0
        for task in self.memory.list_tasks(500):
            if task.status in (TaskStatus.running, TaskStatus.queued):
                task.status, task.error = TaskStatus.failed, "Interrupted by server restart"
                self.memory.save_task(task)
                count += 1
        return count

    async def decide_approval(self, approval_id: str, approve: bool) -> Approval:
        async with self._approval_lock:
            approval = self.memory.get_approval(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            if approval.status != "pending":
                raise ValueError(f"Approval already {approval.status}")
            approval.status = "approved" if approve else "rejected"
            approval.decided_at = now()
            self.memory.save_approval(approval)
        task = self._load(approval.task_id)
        self._emit(task, "approval", f"{approval.title}: {approval.status}", {"approval_id": approval.id})
        self._spawn(self._after_approval(task.id, approval))
        return approval

    async def rollback(self, task_id: str) -> list[str]:
        task = self._load(task_id)
        if not task.checkpoint:
            raise ValueError("Task has no checkpoint")
        restored = self.execution.checkpoints.restore(task.checkpoint)
        self._emit(task, "rollback", f"Restored {len(restored)} file(s) from checkpoint", {"files": restored})
        return restored

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    # ------------------------------------------------------------ pipeline
    async def run(self, task_id: str) -> None:
        task = self._load(task_id)
        try:
            await self._pipeline(task)
        except Exception as exc:  # noqa: BLE001 - surface every failure on the task
            task.status, task.phase, task.error = TaskStatus.failed, "failed", f"{type(exc).__name__}: {exc}"
            self._emit(task, "error", task.error)
            self.memory.save_task(task)

    async def _pipeline(self, task: TaskRecord) -> None:
        task.status = TaskStatus.running
        workspace: Path | None = None
        info: ProjectInfo | None = None
        if task.workspace:
            workspace = resolve_workspace(task.workspace, self.settings.allowed_roots)
            task.workspace = str(workspace)

        # 1. Manager — understand and route
        self._phase(task, "manager")
        manager = await self._call(task, "manager", self._context(task))
        brief = extract_json(manager.content) or {}
        task.resolved_mode = self._route(task.mode, brief)
        self._emit(task, "route", f"Mode resolved to {task.resolved_mode.value}", {"brief": brief})
        fast = task.resolved_mode == Mode.fast
        consensus = task.resolved_mode == Mode.consensus

        # 2. Evidence prelude — read-only inspection, checkpoint when executing
        if workspace:
            self._phase(task, "inspect")
            info, ev = self.execution.inspect(workspace)
            self._add_evidence(task, ev)
            if task.execute:
                task.checkpoint = await self.execution.checkpoints.create(task.id, workspace, info.git)
                self._emit(task, "checkpoint", "Checkpoint created", {"folder": task.checkpoint["folder"]})
                if info.git:
                    for ev in await self.execution.git_evidence(workspace):
                        self._add_evidence(task, ev)

        # 3. Analyst
        analysis = ""
        if not fast:
            self._phase(task, "analyst")
            analysis = (await self._call(task, "analyst", self._context(task, info=info))).content

        # 4. Planner
        self._phase(task, "planner")
        plan_out = await self._call(task, "planner", self._context(task, info=info, prior={"analysis": analysis}))
        plan = extract_json(plan_out.content) or {}
        task.plan = [s for s in plan.get("steps", []) if isinstance(s, dict)] or [
            {"id": 1, "title": plan_out.content[:200], "owner": "team"}]
        files = self._read_files(task, workspace, plan.get("files_to_read", []))
        self.memory.save_task(task)

        # 5. Researcher ∥ Coder(s)
        self._phase(task, "specialists")
        ctx = self._context(task, info=info, files=files, prior={"analysis": analysis, "plan": task.plan})
        coder_models = self.roster.consensus_coders if consensus else [None]
        jobs = [self._call(task, "coder", ctx, model=m) for m in coder_models]
        if not fast:
            jobs.insert(0, self._call(task, "researcher", ctx))
        results = await asyncio.gather(*jobs, return_exceptions=True)
        research = ""
        if not fast:
            first = results.pop(0)
            research = first.content if isinstance(first, AgentOutput) else f"(researcher failed: {first})"
        candidates = [self._parse_plan(r.content) for r in results if isinstance(r, AgentOutput)]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            raise LLMError("No coder produced a valid structured change plan")

        # 6. Reviewer (+ independent cross-review in consensus)
        reviews: list[str] = []
        if not fast:
            self._phase(task, "review")
            review_ctx = self._context(task, info=info, prior={"research": research},
                                       candidates=candidates, evidence=task.evidence)
            review_jobs = [self._call(task, "reviewer", review_ctx)]
            if consensus:
                review_jobs.append(self._call(task, "reviewer", review_ctx, model=self.roster.cross_reviewer,
                                              label="cross_reviewer"))
            for r in await asyncio.gather(*review_jobs, return_exceptions=True):
                reviews.append(r.content if isinstance(r, AgentOutput) else f"(review failed: {r})")

        # 7. Judge — evidence over votes
        chosen = 0
        if not fast:
            self._phase(task, "judge")
            judge = await self._call(task, "judge", self._context(
                task, info=info, candidates=candidates, evidence=task.evidence,
                prior={"reviews": reviews, "research": research}))
            verdict = extract_json(judge.content) or {}
            idx = verdict.get("chosen", 0)
            chosen = idx if isinstance(idx, int) and 0 <= idx < len(candidates) else 0
            self._emit(task, "judge", f"Judge verdict: {verdict.get('verdict', 'n/a')} (candidate {chosen})", verdict)
            if verdict.get("verdict") == "reject":
                candidates[chosen] = ChangePlan(summary="Judge rejected all changes", changes=[])
        task.change_plan = candidates[chosen]
        self.memory.save_task(task)

        await self._apply_or_verify(task, workspace, info)

    async def _apply_or_verify(self, task: TaskRecord, workspace: Path | None, info: ProjectInfo | None) -> None:
        plan = task.change_plan
        if plan and plan.changes:
            if not (task.execute and workspace):
                self._emit(task, "note", "Changes proposed but Execute is off — nothing was written")
            else:
                await self._request_approval(task, workspace, plan)
                return
        if task.execute and workspace and info:
            await self._verify(task, workspace, info)
        await self._finish(task)

    async def _request_approval(self, task: TaskRecord, workspace: Path, plan: ChangePlan) -> None:
        diffs = [self.execution.preview_change(workspace, c) for c in plan.changes]
        approval = Approval(task_id=task.id, action="files.write",
                            title=f"Apply {len(plan.changes)} file change(s): {plan.summary[:120]}",
                            payload=plan.model_dump(), diff="\n".join(diffs)[:200_000])
        self.memory.save_approval(approval)
        task.status, task.phase = TaskStatus.awaiting_approval, "approval"
        self._emit(task, "approval_required", approval.title, {"approval_id": approval.id})
        self.memory.save_task(task)

    async def _after_approval(self, task_id: str, approval: Approval) -> None:
        task = self._load(task_id)
        try:
            task.status = TaskStatus.running
            if approval.status == "rejected":
                self._emit(task, "note", "Changes rejected by user; no files were modified")
                await self._finish(task, rejected=True)
                return
            workspace = resolve_workspace(task.workspace or "", self.settings.allowed_roots)
            plan = ChangePlan.model_validate(approval.payload)
            self._phase(task, "apply")
            for change in plan.changes:
                self._add_evidence(task, self.execution.apply_change(workspace, change, task.checkpoint or {}))
            info, _ = self.execution.inspect(workspace)
            ok = await self._verify(task, workspace, info)
            if not ok and task.repair_round < self.settings.max_repair_rounds:
                task.repair_round += 1
                self._phase(task, f"repair_{task.repair_round}")
                failing = [e for e in task.evidence if e.kind in ("build", "test") and not e.ok]
                files = self._read_files(task, workspace, [c.path for c in plan.changes])
                out = await self._call(task, "coder", self._context(
                    task, info=info, files=files, evidence=failing,
                    prior={"REPAIR": "Verification failed after applying the previous plan. Fix the failures.",
                           "previous_plan": plan.model_dump()}))
                repair = self._parse_plan(out.content)
                if repair and repair.changes:
                    task.change_plan = repair
                    await self._request_approval(task, workspace, repair)
                    return
            await self._finish(task)
        except Exception as exc:  # noqa: BLE001
            task.status, task.phase, task.error = TaskStatus.failed, "failed", f"{type(exc).__name__}: {exc}"
            self._emit(task, "error", task.error)
            self.memory.save_task(task)

    async def _verify(self, task: TaskRecord, workspace: Path, info: ProjectInfo) -> bool:
        self._phase(task, "verify")
        which = tuple(v for v in (task.change_plan.verify if task.change_plan else []) if v in ("build", "test")) \
            or ("build", "test")
        results = await self.execution.verify(workspace, info, which)
        for ev in results:
            self._add_evidence(task, ev)
        if not results:
            self._emit(task, "note", "No build/test commands detected for this project")
        task.verified = all(e.ok for e in results) if results else None
        self.memory.save_task(task)
        return task.verified is not False

    async def _finish(self, task: TaskRecord, rejected: bool = False) -> None:
        self._phase(task, "decision")
        out = await self._call(task, "decision", self._context(task, evidence=task.evidence, prior={
            "change_plan": task.change_plan.model_dump() if task.change_plan else None,
            "user_rejected_changes": rejected}))
        task.decision = out.content
        task.status = TaskStatus.rejected if rejected else TaskStatus.completed
        task.phase = "completed"
        if task.project:
            self.memory.remember(task.project, "decision", f"{task.prompt[:300]}\n→ {task.decision[:1500]}")
        self._emit(task, "done", f"Task {task.status.value}", {"verified": task.verified})
        self.memory.save_task(task)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _route(requested: Mode, brief: dict) -> Mode:
        if requested != Mode.auto:
            return requested
        complexity = str(brief.get("complexity", "medium")).lower()
        return {"low": Mode.fast, "high": Mode.consensus}.get(complexity, Mode.auto)

    async def _call(self, task: TaskRecord, agent: str, user: str, *, model: str | None = None,
                    label: str | None = None) -> AgentOutput:
        spec = self.roster.agents[agent]
        chain = [model, *spec.chain] if model else spec.chain
        chain = list(dict.fromkeys(chain))  # dedupe, keep order
        started = time.time()
        errors: list[str] = []
        for alias in chain:
            try:
                comp = await self.llm.complete(alias, spec.system_prompt, user,
                                               json_mode=agent in ("manager", "planner", "coder", "judge"))
                self.memory.record_model_call(alias, agent, True, comp.duration)
                out = AgentOutput(agent=label or agent, model=comp.model, content=comp.text,
                                  started_at=started, finished_at=time.time())
                task.outputs.append(out)
                self._emit(task, "agent", f"{label or agent} ✓ ({comp.model})", {"agent": label or agent})
                self.memory.save_task(task)
                return out
            except LLMError as exc:
                self.memory.record_model_call(alias, agent, False, time.time() - started)
                errors.append(str(exc))
                self._emit(task, "fallback", f"{agent}: {alias} failed, trying next", {"error": str(exc)})
        out = AgentOutput(agent=label or agent, model=chain[-1], content="", started_at=started,
                          finished_at=time.time(), ok=False, error="; ".join(errors))
        task.outputs.append(out)
        self.memory.save_task(task)
        raise LLMError(f"All models failed for {agent}: {'; '.join(errors)}")

    def _parse_plan(self, text: str) -> ChangePlan | None:
        data = extract_json(text)
        if data is None:
            return None
        try:
            return ChangePlan.model_validate(data)
        except ValidationError:
            return None

    def _read_files(self, task: TaskRecord, workspace: Path | None, paths: list) -> dict[str, str]:
        if not workspace:
            return {}
        files: dict[str, str] = {}
        budget = MAX_FILE_CONTEXT
        for rel in [p for p in paths if isinstance(p, str)][:12]:
            try:
                text = self.execution.read_file(workspace, rel)
            except (PolicyError, OSError) as exc:
                self._emit(task, "note", f"Could not read {rel}: {exc}")
                continue
            files[rel] = text[:budget]
            budget -= len(files[rel])
            if budget <= 0:
                break
        return files

    def _context(self, task: TaskRecord, *, info: ProjectInfo | None = None, files: dict | None = None,
                 prior: dict | None = None, candidates: list[ChangePlan] | None = None,
                 evidence: list[Evidence] | None = None) -> str:
        parts = [f"### TASK\n{task.prompt}"]
        if task.workspace:
            parts.append(f"### WORKSPACE\n{task.workspace} (execute={'on' if task.execute else 'off'})")
        if info:
            parts.append("### PROJECT\n" + json.dumps(info.as_dict(), ensure_ascii=False)[:6000])
            if not files and self.execution:
                try:
                    listing = self.execution.list_files(Path(info.root), 300)
                    parts.append("### FILE LIST\n" + "\n".join(listing))
                except PolicyError:
                    pass
        if task.project:
            mem = self.memory.recall(task.project, 10)
            if mem:
                parts.append("### MEMORY\n" + "\n---\n".join(m["content"] for m in mem))
        if files:
            parts.append("### FILES\n" + "\n\n".join(f"--- {p} ---\n{c}" for p, c in files.items()))
        if prior:
            parts.append("### PRIOR\n" + json.dumps({k: v for k, v in prior.items() if v}, ensure_ascii=False, default=str)[:20_000])
        if candidates is not None:
            parts.append("### CANDIDATES\n" + json.dumps([c.model_dump() for c in candidates], ensure_ascii=False)[:40_000])
        if evidence is not None:
            parts.append("### EVIDENCE\n" + json.dumps(
                [e.model_dump(exclude={"detail"}) | {"detail": e.detail[-3000:]} for e in evidence],
                ensure_ascii=False)[:30_000])
        return "\n\n".join(parts)

    def _add_evidence(self, task: TaskRecord, ev: Evidence) -> None:
        task.evidence.append(ev)
        self._emit(task, "evidence", f"{'✓' if ev.ok else '✗'} {ev.title}: {ev.summary}", {"kind": ev.kind, "ok": ev.ok})
        self.memory.save_task(task)

    def _phase(self, task: TaskRecord, phase: str) -> None:
        task.phase = phase
        self._emit(task, "phase", phase)
        self.memory.save_task(task)

    def _emit(self, task: TaskRecord, kind: str, message: str, data: dict | None = None) -> None:
        self.memory.add_event(Event(task_id=task.id, kind=kind, message=message, data=data or {}))

    def _load(self, task_id: str) -> TaskRecord:
        task = self.memory.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._running.add(t)
        t.add_done_callback(self._running.discard)
