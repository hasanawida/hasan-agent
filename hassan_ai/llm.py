"""Model access.

The orchestrator only ever asks for a role *alias* (``coder``, ``judge`` …).
In live mode aliases are sent to an OpenAI-compatible gateway (LiteLLM), which
maps them to real providers. Each agent may list fallbacks that are tried in
order, so one provider failing never stops the task.

Mock mode returns deterministic, role-aware answers so the whole pipeline can
be exercised offline with zero API cost.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Completion:
    text: str
    model: str
    duration: float
    input_tokens: int = 0
    output_tokens: int = 0
    # API list-price equivalent in USD. For subscription CLIs this is an estimate of
    # what the call would cost on the API, not money actually charged.
    cost_usd: float | None = None


class LLM(Protocol):
    async def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> Completion: ...


class GatewayLLM:
    """OpenAI-compatible chat completions client (LiteLLM proxy, vLLM, Ollama …)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 180.0,
                 transport: httpx.AsyncBaseTransport | None = None, extra_headers: dict | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        headers.update(extra_headers or {})
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout,
                                         transport=transport)

    async def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> Completion:
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        start = time.monotonic()
        try:
            resp = await self._client.post("chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{model}: gateway unreachable ({exc.__class__.__name__})") from exc
        if resp.status_code >= 400:
            raise LLMError(f"{model}: HTTP {resp.status_code} {resp.text[:300]}", status=resp.status_code)
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"{model}: malformed response") from exc
        usage = data.get("usage") or {}
        cost = resp.headers.get("x-litellm-response-cost")
        try:
            cost_usd = float(cost) if cost else None
        except ValueError:
            cost_usd = None
        return Completion(text=text, model=data.get("model", model), duration=time.monotonic() - start,
                          input_tokens=int(usage.get("prompt_tokens") or 0),
                          output_tokens=int(usage.get("completion_tokens") or 0), cost_usd=cost_usd)

    async def aclose(self) -> None:
        await self._client.aclose()


WRITE_DIRECTIVE = re.compile(r"\[\[write\s+([^\]\s]+)\]\](.*?)\[\[/write\]\]", re.DOTALL)


class MockLLM:
    """Deterministic stand-in used when HASSAN_AI_MODE=mock.

    Mock-only convenience: a task prompt containing
    ``[[write path/to/file]]content[[/write]]`` makes the mock Coder propose
    that change, so the approval → apply → verify loop can be demoed and tested.
    """

    async def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> Completion:
        start = time.monotonic()
        role = _role_from_system(system)
        task = _section(user, "TASK")
        text = self._answer(role, model, task, user)
        return Completion(text=text, model=f"mock/{model}", duration=time.monotonic() - start,
                          input_tokens=len(system + user) // 4, output_tokens=len(text) // 4, cost_usd=0.0)

    def _answer(self, role: str, model: str, task: str, user: str) -> str:
        short = task.strip().splitlines()[0][:140] if task.strip() else "the task"
        if role == "manager":
            complexity = "high" if len(task) > 400 or any(k in task.lower() for k in ("critical", "security", "مهم", "حساس")) else "medium"
            return json.dumps({"goal": short, "complexity": complexity,
                               "needs_execution": "workspace" in user.lower(),
                               "notes": "Route through the full team; evidence decides."}, ensure_ascii=False)
        if role == "analyst":
            return (f"Analysis of: {short}\n- Constraints: keep working parts unchanged, checkpoint first.\n"
                    "- Risks: regressions in untouched modules; verify with build/test.\n"
                    "- Success criteria: task goal met and all detected checks pass.")
        if role == "planner":
            files = [m.group(1) for m in WRITE_DIRECTIVE.finditer(task)]
            return json.dumps({"steps": [
                {"id": 1, "title": "Inspect project and gather evidence", "owner": "execution"},
                {"id": 2, "title": "Research context and constraints", "owner": "researcher"},
                {"id": 3, "title": "Prepare structured change plan", "owner": "coder"},
                {"id": 4, "title": "Review and cross-check", "owner": "reviewer"},
                {"id": 5, "title": "Judge on evidence, then verify build/tests", "owner": "judge"},
            ], "files_to_read": files}, ensure_ascii=False)
        if role == "researcher":
            return f"Research notes for '{short}': no external lookup in mock mode; relying on workspace evidence."
        if role == "coder":
            changes = [{"path": m.group(1), "action": "write", "content": m.group(2).lstrip("\n"),
                        "reason": "Requested explicitly in the task"} for m in WRITE_DIRECTIVE.finditer(task)]
            if "REPAIR" in user and changes:
                changes = [{**c, "reason": "Repair attempt after failed verification"} for c in changes]
            return json.dumps({"summary": f"[{model}] " + ("Proposed file changes" if changes else "No file changes needed; verification only."),
                               "changes": changes, "verify": ["build", "test"]}, ensure_ascii=False)
        if role == "reviewer":
            return f"[{model}] Review: plan is scoped, reversible (checkpoint + backups), and verified by build/test. No blocking issues."
        if role == "judge":
            return json.dumps({"chosen": 0, "verdict": "approve",
                               "reasons": ["Candidate 0 is consistent with evidence", "Reviewers raised no blockers"]})
        if role == "operator":
            # Mock-only: task lines `OP {"tool": ..., "args": {...}}` are executed in step 1.
            history = _section(user, "HISTORY")
            if "(nothing yet)" in history:
                ops = [json.loads(m.group(1)) for m in re.finditer(r"^OP (\{.*\})\s*$", task, re.MULTILINE)]
                return json.dumps({"thought": "look first", "actions": ops or [{"tool": "system_info", "args": {}}]},
                                  ensure_ascii=False)
            return json.dumps({"done": True, "answer": f"[{model}] Done: {short}"}, ensure_ascii=False)
        if role == "decision":
            ev = _section(user, "EVIDENCE")
            failed = ev.count('"ok": false')
            state = "DONE" if failed == 0 else f"NEEDS ATTENTION ({failed} failing check(s))"
            return f"Final decision: {state}.\nTask: {short}\nEvidence was reviewed; see the evidence panel for details."
        return f"[{model}] ok"


def _role_from_system(system: str) -> str:
    m = re.search(r"ROLE:\s*(\w+)", system)
    return m.group(1).lower() if m else "unknown"


def _section(text: str, name: str) -> str:
    m = re.search(rf"### {name}\n(.*?)(?=\n### |\Z)", text, re.DOTALL)
    return m.group(1) if m else ""


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model answer (handles ``` fences)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                candidates.append(text[start:i + 1])
                break
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None
