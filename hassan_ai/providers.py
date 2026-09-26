"""Model backends and the alias router.

Backends
--------
* ``claude_cli``  – the Claude Code CLI on this machine (``claude -p``). Uses
  whatever the CLI is logged in with, e.g. a Claude Pro/Max subscription.
* ``codex_cli``   – the OpenAI Codex CLI (``codex exec``). Uses its login,
  e.g. "Sign in with ChatGPT" on a Plus/Pro plan.
* ``gateway``     – any OpenAI-compatible endpoint (LiteLLM, Ollama, OpenRouter …).
* ``mock``        – deterministic offline answers.

CLI backends are used purely as *brains*: they run in an empty scratch folder
with tools disabled / a read-only sandbox, receive the prompt on stdin and only
return text. All file changes still go through Hassan's diff + approval flow.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_yaml
from .execution import NO_WINDOW
from .llm import Completion, GatewayLLM, LLMError, MockLLM

# Removed from the CLI's environment when use_subscription is on, so the CLI
# bills the logged-in subscription instead of silently using an API key.
API_KEY_VARS = {
    "claude_cli": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "codex_cli": ("OPENAI_API_KEY", "CODEX_API_KEY"),
}


def _combined(system: str, user: str) -> str:
    return f"<instructions>\n{system}\n</instructions>\n\n{user}"


@dataclass
class CLIBackend:
    kind: str  # claude_cli | codex_cli
    command: str
    default_model: str | None = None
    timeout: float = 600.0
    use_subscription: bool = True
    extra_args: list[str] = field(default_factory=list)

    def executable(self) -> str | None:
        return shutil.which(self.command)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.use_subscription:
            for var in API_KEY_VARS.get(self.kind, ()):
                env.pop(var, None)
        env.setdefault("NO_COLOR", "1")
        return env

    async def complete(self, model: str | None, system: str, user: str, *, json_mode: bool = False) -> Completion:
        exe = self.executable()
        if exe is None:
            raise LLMError(f"{self.kind}: '{self.command}' not found on PATH (is it installed?)")
        model = model or self.default_model
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hassan-brain-") as scratch:
            scratch_path = Path(scratch)
            if self.kind == "claude_cli":
                (scratch_path / "system.txt").write_text(system, encoding="utf-8")
                argv = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                        "--system-prompt-file", str(scratch_path / "system.txt")]
                if model:
                    argv += ["--model", model]
                stdin = user
            else:
                out_file = scratch_path / "last_message.txt"
                argv = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
                        "--json", "-C", str(scratch_path), "-o", str(out_file)]
                if model:
                    argv += ["-m", model]
                argv.append("-")
                stdin = _combined(system, user)
            argv += self.extra_args

            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=scratch, env=self._env(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **NO_WINDOW)
            try:
                out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), self.timeout)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.communicate()
                raise LLMError(f"{self.kind}: timed out after {self.timeout:.0f}s") from exc
            stdout = out.decode("utf-8", "replace")
            stderr = err.decode("utf-8", "replace")
            label = f"{self.kind}/{model or 'default'}"

            if self.kind == "claude_cli":
                completion = self._parse_claude(stdout, stderr, proc.returncode, model)
            else:
                if proc.returncode != 0:
                    raise LLMError(f"{label}: exit {proc.returncode}: {(stderr or stdout)[-400:]}")
                text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
                completion = self._parse_codex(stdout, text, label)
        if not completion.text.strip():
            raise LLMError(f"{label}: empty answer")
        completion.duration = time.monotonic() - start
        return completion

    def _parse_claude(self, stdout: str, stderr: str, code: int | None, model: str | None) -> Completion:
        try:
            data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        except ValueError:
            data = {}
        if data.get("is_error") or code != 0 or "result" not in data:
            detail = data.get("result") or stderr or stdout
            raise LLMError(f"claude_cli: exit {code}: {str(detail)[-400:]}")
        usage = data.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        used_models = list((data.get("modelUsage") or {}).keys())
        name = used_models[0] if len(used_models) == 1 else (model or "default")
        cost = data.get("total_cost_usd")
        return Completion(text=str(data["result"]), model=f"claude_cli/{name}", duration=0.0,
                          input_tokens=tokens_in, output_tokens=int(usage.get("output_tokens") or 0),
                          cost_usd=float(cost) if cost is not None else None)

    @staticmethod
    def _parse_codex(stdout: str, text: str, label: str) -> Completion:
        """`codex exec --json` prints JSONL events; token usage arrives on turn events."""
        tokens_in = tokens_out = 0
        last_message = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            usage = event.get("usage") or (event.get("msg") or {}).get("usage") or {}
            if isinstance(usage, dict) and ("input_tokens" in usage or "output_tokens" in usage):
                tokens_in += int(usage.get("input_tokens") or 0)
                tokens_out += int(usage.get("output_tokens") or 0)
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
                last_message = item["text"]
        return Completion(text=text or last_message, model=label, duration=0.0,
                          input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=None)

    async def status(self) -> dict:
        exe = self.executable()
        info: dict = {"type": self.kind, "command": self.command, "installed": exe is not None,
                      "use_subscription": self.use_subscription}
        if not exe:
            return info
        checks = [["--version"]] + ([["login", "status"]] if self.kind == "codex_cli" else [])
        for args in checks:
            try:
                proc = await asyncio.create_subprocess_exec(
                    exe, *args, env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **NO_WINDOW)
                out, err = await asyncio.wait_for(proc.communicate(), 20)
                info[" ".join(args).lstrip("-")] = (out or err).decode("utf-8", "replace").strip()[:200]
            except (OSError, asyncio.TimeoutError) as exc:
                info[" ".join(args).lstrip("-")] = f"error: {exc}"
        return info


@dataclass
class Route:
    backend: str
    model: str | None = None


class RouterLLM:
    """Sends each alias to the backend configured in configs/providers.yaml."""

    def __init__(self, backends: dict, routes: dict[str, Route], default: Route):
        self.backends = backends
        self.routes = routes
        self.default = default

    @classmethod
    def from_config(cls, path: Path, gateway_url: str, gateway_key: str, timeout: float) -> "RouterLLM":
        data = load_yaml(path)
        backends: dict = {}
        for name, cfg in (data.get("backends") or {"gateway": {"type": "gateway"}}).items():
            kind = cfg.get("type", name)
            if kind in ("claude_cli", "codex_cli"):
                backends[name] = CLIBackend(
                    kind=kind, command=cfg.get("command", "claude" if kind == "claude_cli" else "codex"),
                    default_model=cfg.get("model"), timeout=float(cfg.get("timeout", timeout)),
                    use_subscription=bool(cfg.get("use_subscription", True)),
                    extra_args=[str(a) for a in cfg.get("extra_args", [])])
            elif kind == "gateway":
                backends[name] = GatewayLLM(cfg.get("url", gateway_url).rstrip("/"),
                                            os.environ.get(cfg["key_env"], "") if cfg.get("key_env") else gateway_key,
                                            timeout)
            elif kind == "mock":
                backends[name] = MockLLM()
            else:
                raise ValueError(f"Unknown backend type: {kind}")

        def parse(value) -> Route:
            if isinstance(value, str):
                return Route(backend=value)
            return Route(backend=value["backend"], model=value.get("model"))

        routes = {alias: parse(v) for alias, v in (data.get("aliases") or {}).items()}
        default = parse(data.get("default", next(iter(backends))))
        for route in [default, *routes.values()]:
            if route.backend not in backends:
                raise ValueError(f"providers.yaml routes to unknown backend '{route.backend}'")
        return cls(backends, routes, default)

    def route_for(self, alias: str) -> Route:
        return self.routes.get(alias, self.default)

    async def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> Completion:
        route = self.route_for(model)
        backend = self.backends[route.backend]
        if isinstance(backend, CLIBackend):
            return await backend.complete(route.model, system, user, json_mode=json_mode)
        # gateway / mock: the gateway maps the alias (or an explicit model) itself
        return await backend.complete(route.model or model, system, user, json_mode=json_mode)

    async def status(self) -> dict:
        backends = {}
        for name, backend in self.backends.items():
            if isinstance(backend, CLIBackend):
                backends[name] = await backend.status()
            else:
                backends[name] = {"type": "gateway" if isinstance(backend, GatewayLLM) else "mock"}
        return {"default": self.default.__dict__,
                "aliases": {a: r.__dict__ for a, r in self.routes.items()},
                "backends": backends}

    async def aclose(self) -> None:
        for backend in self.backends.values():
            if hasattr(backend, "aclose"):
                await backend.aclose()
