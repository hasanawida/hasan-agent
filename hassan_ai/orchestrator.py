"""Hassan Orchestrator: Manager → Analyst → Planner → (Researcher ∥ Coder) →
Reviewer → Judge → [approval → apply → verify → repair] → Decision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

from pydantic import ValidationError

from .agents import Roster
from .config import Settings
from .execution import ExecutionManager, ProjectInfo
from .llm import LLM, LLMError, PaidRequired, extract_json
from .memory import Memory
from .policy import PolicyError, resolve_workspace
from .schemas import (AgentOutput, Approval, ChangePlan, Event, Evidence, Mode, TaskCreate,
                      TaskRecord, TaskStatus, now)

MAX_FILE_CONTEXT = 40_000
TIER_ORDER = ("simple", "medium", "complex")
BUDGET_TIER = {"free": "simple", "balanced": "medium", "best": "complex"}
COMPLEX_WORDS = ("برمج", "كود", "code", "مشروع", "project", "build", "بناء", "صمم", "تصميم", "design", "خطة", "خطط",
                 "plan", "تحليل", "حلل", "analy", "blender", "بلندر", "رندر", "render", "refactor", "debug", "bug",
                 "خلل", "اصلح", "أصلح", "صلح", "fix", "architecture", "معمارية", "قارن", "مقارنة", "compare",
                 "research", "بحث معمق", "ابحث عن", "تقرير مفصل", "استراتيجية", "strategy", "api", "database")
SIMPLE_WORDS = ("افتح", "open", "كاميرا", "كميرا", "camera", "مايك", "ميكروفون", "مايكروفون", "mic", "سجّل",
                "سجل ", "صوّر", "صور ", "قديش", "كم ", "شو في", "list", "screenshot", "صورة للشاشة", "صور الشاشة", "وين",
                "where", "الساعة", "مساحة", "space", "اعرض", "show", "شغّل", "شغل ", "سكّر", "close", "ترجم",
                "translate", "ذكرني", "remind", "اسم", "حجم", "size", "كم الساعة", "what time")
TRIAGE_SYSTEM = ("ROLE: triage\nClassify how much thinking this task needs. Answer ONLY JSON: "
                 '{"level": "simple"|"medium"|"complex"}. simple = a quick answer or one/two small PC actions; '
                 "medium = several steps, light research or file organising; complex = planning, coding, "
                 "analysis, design, 3D work or anything important and long.")


def classify_heuristic(prompt: str, kind: str) -> tuple[str | None, str]:
    """Free first pass. Returns (tier or None when unsure, reason)."""
    text = prompt.lower()
    complex_hit = next((w for w in COMPLEX_WORDS if w in text), None)
    simple_hit = next((w for w in SIMPLE_WORDS if w in text), None)
    if len(prompt) > 500 or text.count("\n") > 6:
        return "complex", "مهمة طويلة"
    if complex_hit and not simple_hit:
        return "complex", f"فيها «{complex_hit.strip()}»"
    if simple_hit and not complex_hit and len(prompt) < 160:
        return ("medium" if kind == "project" else "simple"), f"مهمة قصيرة («{simple_hit.strip()}»)"
    return None, ""


class Orchestrator:
    def __init__(self, settings: Settings, memory: Memory, llm: LLM, roster: Roster, execution: ExecutionManager):
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.roster = roster
        self.execution = execution
        self._running: set[asyncio.Task] = set()
        self._jobs: dict[str, asyncio.Task] = {}
        self._approval_lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Future] = {}  # operator approvals being waited on
        self._cancelled: set[str] = set()
        self.operator = None  # set by the server (Operator mode)
        self.listeners: list = []  # callables(task, kind, message, data)
        self._paid_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ API
    def submit(self, req: TaskCreate) -> TaskRecord:
        if req.device_id and req.kind != "operate":
            raise ValueError("Phone tasks must use operate mode")
        task = TaskRecord(prompt=req.prompt, mode=req.mode, workspace=req.workspace,
                          execute=req.execute, project=req.project, kind=req.kind, origin=req.origin,
                          budget=req.budget, conversation=req.conversation, device_id=req.device_id)
        self.memory.save_task(task)
        self._emit(task, "created", "Task received", {"mode": task.mode.value, "execute": task.execute})
        self._spawn(self.run(task.id), task.id)
        return task

    def resume(self, task_id: str, resolution: str | None = None) -> TaskRecord:
        """Start a linked continuation only after an explicit request, never during recovery."""
        previous = self._load(task_id)
        if previous.kind != "operate" or previous.status not in (
                TaskStatus.failed, TaskStatus.incomplete, TaskStatus.cancelled):
            raise ValueError("Only interrupted, failed or incomplete operator tasks can be continued")
        if task_id in self._jobs:
            raise ValueError("Wait for the previous task to finish stopping")
        if previous.resume_child:
            return self._load(previous.resume_child)  # idempotent double click; resume the child for a later attempt
        pending = previous.operator_pending
        uncertain = bool(pending and pending.get("mutation"))
        if uncertain and resolution not in {"completed", "not_completed"}:
            raise ValueError("Last action is uncertain; check the device and explicitly resolve completed or not_completed")
        if not uncertain and resolution is not None:
            raise ValueError("This task has no uncertain action to resolve")
        if previous.device_id:
            hub = self.operator.phone_hub if self.operator else None
            device = next((d for d in hub.list_devices() if d["device_id"] == previous.device_id), None) if hub else None
            if not device or not device["online"] or not device["control_enabled"] or device.get("busy"):
                raise ValueError("The same phone must be online, enabled and free before continuing")
        history = copy.deepcopy(previous.operator_history)
        completed = list(previous.operator_completed)
        if uncertain:
            confirmed = resolution == "completed"
            history.append({"tool": pending["tool"], "args": pending["args"], "mutation": True,
                            "ok": confirmed, "acknowledged": confirmed,
                            "result": "User checked the device: " + resolution})
            if confirmed and pending["signature"] not in completed:
                completed.append(pending["signature"])
            previous.operator_pending = {**pending, "resolution": resolution, "resolved_at": now()}
            previous.resume_blocked = False
        resumed = TaskRecord(prompt=previous.prompt, mode=previous.mode, kind=previous.kind,
                             device_id=previous.device_id, workspace=previous.workspace, project=previous.project,
                             execute=previous.execute, origin=previous.origin, budget=previous.budget,
                             conversation=previous.conversation, operator_history=history,
                             operator_completed=completed, resume_of=previous.id,
                             resume_count=previous.resume_count + 1, phase="resuming")
        previous.resume_child = resumed.id
        self.memory.save_resumption(previous, resumed)
        self._emit(resumed, "resumed", "Continuing after fresh observation; acknowledged changes will not be replayed",
                   {"previous_task_id": previous.id, "resolution": resolution})
        self._spawn(self.run(resumed.id), resumed.id)
        return resumed

    def recover_interrupted(self) -> int:
        """Tasks cut off by a restart are marked failed (awaiting-approval tasks survive)."""
        count = 0
        for task in self.memory.list_tasks(500):
            waiting = [a for a in self.memory.approvals(task.id, status="pending")
                       if a.payload.get("operator") or a.payload.get("wait")]
            if task.status == TaskStatus.awaiting_approval and (task.kind == "operate" or waiting):
                # the operator loop that was waiting is gone; don't leave a live-looking approval
                for a in self.memory.approvals(task.id, status="pending"):
                    a.status, a.decided_at = "rejected", now()
                    self.memory.save_approval(a)
                task.status = TaskStatus.running
            if task.status in (TaskStatus.running, TaskStatus.queued, TaskStatus.cancelling):
                task.status, task.phase, task.error = TaskStatus.failed, "interrupted", "Interrupted by server restart"
                task.resume_blocked = bool(task.operator_pending and task.operator_pending.get("mutation"))
                self.memory.save_task(task)
                count += 1
        return count

    async def decide_approval(self, approval_id: str, approve: bool, trust_similar: bool = False) -> Approval:
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
        if approval.payload.get("operator") or approval.payload.get("wait"):
            if approve and trust_similar and self.operator is not None:
                self.operator.trust(task.id, approval.payload)
            waiter = self._waiters.get(approval.id)
            if waiter and not waiter.done():
                waiter.set_result(approve)
            return approval
        self._spawn(self._after_approval(task.id, approval), task.id)
        return approval

    def cancel(self, task_id: str) -> None:
        """Request cancellation; only report stopped after active work is cleaned up."""
        task = self._load(task_id)
        if task.status not in (TaskStatus.queued, TaskStatus.running, TaskStatus.awaiting_approval):
            return
        self._cancelled.add(task_id)
        task.status, task.phase = TaskStatus.cancelling, "cancelling"
        self.memory.save_task(task)
        for a in self.memory.approvals(task_id, status="pending"):
            a.status, a.decided_at = "rejected", now()
            self.memory.save_approval(a)
        self._emit(task, "cancel", "جارٍ إيقاف التنفيذ وتنظيف العمليات الجارية")
        job = self._jobs.get(task_id)
        if job is not None and not job.done():
            job.cancel()
        else:
            self._mark_cancelled(task_id)

    async def take_over_desktop(self, task_id: str) -> None:
        """Manual input waits for the agent's process/tool cleanup before taking ownership."""
        job = self._jobs.get(task_id)
        if job is None:
            return  # DesktopControl still checks the lease; stale owners are never overridden here.
        self.cancel(task_id)
        await asyncio.gather(asyncio.shield(job), return_exceptions=True)

    def _mark_cancelled(self, task_id: str) -> None:
        task = self._load(task_id)
        task.status, task.phase = TaskStatus.cancelled, "cancelled"
        task.decision = "أوقفت المهمة بطلب منك. التغييرات التي نُفّذت قبل الإيقاف تبقى محفوظة."
        self.memory.save_task(task)
        self._emit(task, "done", task.decision)
        self._cancelled.discard(task_id)

    def cancelled(self, task_id: str) -> bool:
        return task_id in self._cancelled

    async def rollback(self, task_id: str) -> list[str]:
        task = self._load(task_id)
        if not task.checkpoint:
            raise ValueError("Task has no checkpoint")
        restored = self.execution.checkpoints.restore(task.checkpoint)
        self._emit(task, "rollback", f"Restored {len(restored)} file(s) from checkpoint", {"files": restored})
        return restored

    async def drain(self) -> None:
        for task_id in list(self._jobs):
            self.cancel(task_id)
        if self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    # ------------------------------------------------------------ pipeline
    async def run(self, task_id: str) -> None:
        task = self._load(task_id)
        try:
            await self._triage(task)
            if task.kind == "project" and not task.workspace and task.tier == "simple" and self.operator is not None:
                # A quick question about the PC ("how much free space?") needs someone who can look at
                # the machine and answer, not a team that writes a program for it.
                task.kind = "operate"
                self._emit(task, "route", "🖥️ سؤال سريع عن الكمبيوتر — حوّلته للمشغّل بدل الفريق")
                self.memory.save_task(task)
            if task.kind == "operate":
                if self.operator is None:
                    raise RuntimeError("Operator mode is not available")
                await self.operator.run(task)
                return
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
        if task.mode == Mode.auto and task.tier == "simple":
            task.resolved_mode = Mode.fast  # an easy task doesn't need the whole team
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
        task.status = TaskStatus.rejected if rejected else (TaskStatus.failed if task.verified is False else TaskStatus.completed)
        task.phase = task.status.value
        if task.project:
            self.memory.remember(task.project, "decision", f"{task.prompt[:300]}\n→ {task.decision[:1500]}")
        self._emit(task, "done", f"Task {task.status.value}", {"verified": task.verified})
        self.memory.save_task(task)

    # -------------------------------------------------------------- helpers
    # ---- cost-aware triage ---------------------------------------------------
    @property
    def tiered(self) -> bool:
        return bool(getattr(self.llm, "tiers", None))

    async def _triage(self, task: TaskRecord) -> None:
        if task.tier:  # already decided (e.g. resumed)
            return
        if task.budget in BUDGET_TIER:
            tier, reason = BUDGET_TIER[task.budget], "اختيارك"
        else:
            # classify by content; a team task without a project folder may still be a quick PC question
            tier, reason = classify_heuristic(task.prompt, "operate" if not task.workspace else task.kind)
            if tier is None and self.tiered:
                try:
                    # a quick free guess only: never a paid brain, never more than 30 s
                    alias = "triage@simple#free" if self.gated else "triage@simple"
                    comp = await asyncio.wait_for(self.llm.complete(alias, TRIAGE_SYSTEM, task.prompt[:4000]), 30)
                    level = str((extract_json(comp.text) or {}).get("level", "")).lower()
                    self.memory.record_usage(task.id, "triage", comp.model, comp.input_tokens, comp.output_tokens,
                                             comp.cost_usd, comp.duration)
                    if level in TIER_ORDER:
                        tier, reason = level, f"فرز سريع ({comp.model})"
                except (LLMError, asyncio.TimeoutError):
                    pass
            if tier is None:
                tier, reason = "medium", "افتراضي"
        task.tier = tier
        icon = {"simple": "🆓", "medium": "⚖️", "complex": "🧠"}[tier]
        self._emit(task, "tier", f"{icon} مستوى المهمة: {tier} — {reason}", {"tier": tier})
        if self.gated:
            task.paid_ok = task.budget in ("balanced", "best")
            if not task.paid_ok:
                self._emit(task, "tier", "🆓 شغّال على العقول المجانية" +
                           (" بس (اختيارك)" if task.budget == "free" else " — وبسألك قبل أي عقل مدفوع"))
        self.memory.save_task(task)
        if self.gated and not task.paid_ok and tier == "complex":
            await self.ask_paid(task, "المهمة صعبة (تخطيط/برمجة/تحليل) والعقول المجانية ممكن تطلع نتيجة أضعف")

    @property
    def gated(self) -> bool:
        """True when the brains include paid ones that need Hassan's OK."""
        return bool(getattr(self.llm, "paid", None))

    async def ask_paid(self, task: TaskRecord, reason: str) -> bool:
        """Ask Hassan (dashboard / phone / Telegram) before spending paid brains. Once per task."""
        lock = self._paid_locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            if task.paid_ok or task.paid_asked:
                return bool(task.paid_ok)
            earlier = self._chat_paid_answer(task)
            if earlier is not None:  # already answered in this chat: don't ask again every message
                task.paid_asked, task.paid_ok = True, earlier
                self._emit(task, "tier", "💳 وافقت على المدفوع بهاي المحادثة" if earlier
                           else "🆓 رفضت المدفوع بهاي المحادثة — بكمّل بالمجاني")
                self.memory.save_task(task)
                return earlier
            if task.budget == "free":
                self._emit(task, "tier", f"⛔ ما استخدمت عقل مدفوع لأنك اخترت «مجاني بس»: {reason}")
                return False
            paid = ", ".join(sorted(getattr(self.llm, "paid", []))) or "Claude / ChatGPT"
            approval = Approval(task_id=task.id, action="brain.paid",
                                title=f"💳 بدي أستخدم عقل مدفوع لهاي المهمة — {reason}",
                                payload={"wait": True, "paid_brain": True},
                                diff=(f"العقول المدفوعة: {paid}\nجوابك بيسري على هاي المحادثة (لحد «محادثة جديدة»). "
                                      "إذا رفضت بكمّل بالمجاني قد ما بقدر."))
            self.memory.save_approval(approval)
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._waiters[approval.id] = future
            previous = task.status
            task.status = TaskStatus.awaiting_approval
            self._emit(task, "approval_required", approval.title, {"approval_id": approval.id})
            self.memory.save_task(task)
            try:
                approved = bool(await future)
            finally:
                self._waiters.pop(approval.id, None)
            task.status = previous if previous != TaskStatus.awaiting_approval else TaskStatus.running
            task.paid_asked, task.paid_ok = True, approved
            self._emit(task, "tier", "💳 موافق على المدفوع لهاي المهمة" if approved else "🆓 رفضت المدفوع — بكمّل بالمجاني")
            self.memory.save_task(task)
            return approved

    def _chat_paid_answer(self, task: TaskRecord) -> bool | None:
        """Hassan's paid-brain answer from an earlier message of the same chat, if any."""
        if not task.conversation:
            return None
        for earlier in reversed(self.memory.conversation(task.conversation, before=task.id, limit=30)):
            if earlier.paid_asked:
                return bool(earlier.paid_ok)
        return None

    async def escalate(self, task: TaskRecord, why: str) -> bool:
        """Move the task to a stronger brain. With paid gating, that means asking for paid brains."""
        if self.gated and not task.paid_ok:
            free = self.llm.free_brains() if hasattr(self.llm, "free_brains") else []
            if task.free_skip + 1 < len(free):  # another free brain first, paid only as the last resort
                task.free_skip += 1
                self._emit(task, "tier", f"🔁 {why} — بجرّب عقل مجاني تاني ({free[task.free_skip]})")
                self.memory.save_task(task)
                return True
            if not await self.ask_paid(task, f"العقول المجانية ما قدرت: {why}"):
                return False
            if task.tier in TIER_ORDER and task.tier != "complex":
                task.tier = TIER_ORDER[TIER_ORDER.index(task.tier) + 1]
                self.memory.save_task(task)
            return True
        i = TIER_ORDER.index(task.tier) if task.tier in TIER_ORDER else len(TIER_ORDER) - 1
        cap = TIER_ORDER.index(BUDGET_TIER.get(task.budget, "complex")) if task.budget in ("free", "balanced") \
            else len(TIER_ORDER) - 1  # Hassan's budget choice is a hard ceiling
        if i >= cap:
            if i < len(TIER_ORDER) - 1:
                self._emit(task, "tier", f"⛔ ما رقّيت لعقل أغلى لأنك اخترت «{task.budget}»: {why}")
            return False
        task.tier = TIER_ORDER[i + 1]
        self._emit(task, "tier", f"⬆️ ترقية لعقل أقوى ({task.tier}): {why}", {"tier": task.tier})
        self.memory.save_task(task)
        return True

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
        if self.gated and not task.paid_ok:
            # free brains only; the router refuses paid ones and says so (PaidRequired)
            chain = [f"{spec.model}@{task.tier or 'simple'}#free" + (f"~{task.free_skip}" if task.free_skip else "")]
        elif self.tiered and self.llm.tier_uses_list(task.tier):
            # cheap tiers: one call; the router walks the tier's brain list itself
            chain = [f"{spec.model}@{task.tier}"]
        started = time.time()
        errors: list[str] = []
        for alias in chain:
            try:
                system = self.operator.system_prompt(task) if agent == "operator" and task.device_id and self.operator else spec.system_prompt
                comp = await self.llm.complete(alias, system, user,
                                               json_mode=agent in ("manager", "planner", "coder", "judge"))
                self.memory.record_model_call(alias, agent, True, comp.duration)
                self.memory.record_usage(task.id, label or agent, comp.model, comp.input_tokens,
                                         comp.output_tokens, comp.cost_usd, comp.duration)
                out = AgentOutput(agent=label or agent, model=comp.model, content=comp.text,
                                  started_at=started, finished_at=time.time(),
                                  input_tokens=comp.input_tokens, output_tokens=comp.output_tokens,
                                  cost_usd=comp.cost_usd)
                task.outputs.append(out)
                self._emit(task, "agent", f"{label or agent} ✓ ({comp.model})", {"agent": label or agent})
                self.memory.save_task(task)
                return out
            except LLMError as exc:
                self.memory.record_model_call(alias, agent, False, time.time() - started)
                errors.append(str(exc))
                self._emit(task, "fallback", f"{agent}: {alias} failed ({str(exc)[:300]}), trying next",
                           {"error": str(exc)})
        out = AgentOutput(agent=label or agent, model=chain[-1], content="", started_at=started,
                          finished_at=time.time(), ok=False, error="; ".join(errors))
        task.outputs.append(out)
        self.memory.save_task(task)
        if self.gated and not task.paid_ok:
            if await self.ask_paid(task, f"{agent}: " + ("ما في عقل مجاني جاهز" if any("ما في عقل مجاني" in e for e in errors)
                                                      else "العقول المجانية فشلت")):
                return await self._call(task, agent, user, model=model, label=label)
        elif self.tiered and self.llm.tier_uses_list(task.tier) and await self.escalate(task, f"{agent}: كل العقول الأرخص فشلت"):
            return await self._call(task, agent, user, model=model, label=label)
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
        for listener in list(self.listeners):  # e.g. the Telegram bot
            try:
                listener(task, kind, message, data or {})
            except Exception:  # noqa: BLE001 - a notifier must never break a task
                pass

    def _load(self, task_id: str) -> TaskRecord:
        task = self.memory.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _spawn(self, coro, task_id: str) -> None:
        started = False

        async def run_job():
            nonlocal started
            started = True
            await coro

        def finished(job):
            if not started:
                coro.close()
            self._running.discard(job)
            if self._jobs.get(task_id) is job:
                self._jobs.pop(task_id, None)
            if job.cancelled():
                self._mark_cancelled(task_id)
            elif job.exception() is not None:
                task = self._load(task_id)
                task.status, task.phase = TaskStatus.failed, "failed"
                task.error = f"Execution cleanup failed: {job.exception()}"
                self.memory.save_task(task)
                self._emit(task, "error", task.error)
            self._cancelled.discard(task_id)
            self._paid_locks.pop(task_id, None)
            if self.operator is not None:
                self.operator.cleanup(task_id)

        job = asyncio.get_running_loop().create_task(run_job())
        self._jobs[task_id] = job
        self._running.add(job)
        job.add_done_callback(finished)
