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
    "gemini_cli": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"),
}
CLI_KINDS = ("claude_cli", "codex_cli", "gemini_cli")
DEFAULT_COMMAND = {"claude_cli": "claude", "codex_cli": "codex", "gemini_cli": "gemini"}


# Codex and Gemini CLIs are agents with their own tools and approval systems. Here they are
# only the *brain*: if one tries to act itself (open Edge, run a command…) its own sandbox
# refuses and it reports "approval refused" instead of giving Hassan's operator the steps.
TEXT_ONLY_NOTE = ("IMPORTANT: you are used as a text-only brain inside Hassan AI OS. Do NOT run commands, "
                  "open apps, browse, read files or use any of your own tools, and do not ask for approvals. "
                  "The host program performs every action itself after Hassan approves it. "
                  "Only reply with the text/JSON requested below.")


def _combined(system: str, user: str) -> str:
    return f"<instructions>\n{TEXT_ONLY_NOTE}\n\n{system}\n</instructions>\n\n{user}"


@dataclass
class CLIBackend:
    kind: str  # claude_cli | codex_cli | gemini_cli
    command: str
    default_model: str | None = None
    timeout: float = 600.0
    use_subscription: bool = True
    extra_args: list[str] = field(default_factory=list)
    # Subscription usage limit hit: skip this backend (instant fallback) and re-check later.
    limited_until: float = 0.0
    limit_message: str = ""
    LIMIT_RECHECK = 1800.0

    def _note_limit(self, message: str) -> None:
        self.limited_until = time.time() + self.LIMIT_RECHECK
        self.limit_message = message.strip()[:200]

    @staticmethod
    def _looks_like_limit(text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ("usage limit", "weekly limit", "rate limit", "hit your", "limit reached",
                                    "quota", "429", "resource_exhausted"))

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
        if time.time() < self.limited_until:
            raise LLMError(f"{self.kind}: usage limit — {self.limit_message}")
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
            elif self.kind == "gemini_cli":
                # plan = read-only; the prompt arrives on stdin, -p just switches to headless mode
                argv = [exe, "-p", "Follow the instructions above.", "-o", "json", "--approval-mode", "plan"]
                if model:
                    argv += ["-m", model]
                stdin = _combined(system, user)
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
            elif self.kind == "gemini_cli":
                completion = self._parse_gemini(stdout, stderr, proc.returncode, model)
            else:
                if proc.returncode != 0:
                    detail = (stderr or stdout)[-400:]
                    if self._looks_like_limit(detail):
                        self._note_limit(detail.strip().splitlines()[-1] if detail.strip() else "limit")
                    raise LLMError(f"{label}: exit {proc.returncode}: {detail}")
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
            if data.get("api_error_status") == 429 or self._looks_like_limit(str(detail)):
                self._note_limit(str(detail))
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

    def _parse_gemini(self, stdout: str, stderr: str, code: int | None, model: str | None) -> Completion:
        start, end = stdout.find("{"), stdout.rfind("}")
        try:
            data = json.loads(stdout[start:end + 1]) if start != -1 and end > start else {}
        except ValueError:
            data = {}
        error = data.get("error")
        if error or "response" not in data:
            detail = (error or {}).get("message") if isinstance(error, dict) else (error or stderr or stdout)
            if self._looks_like_limit(str(detail)):
                self._note_limit(str(detail))
            raise LLMError(f"gemini_cli: exit {code}: {str(detail)[-400:]}")
        tokens_in = tokens_out = 0
        used = []
        for name, m in ((data.get("stats") or {}).get("models") or {}).items():
            used.append(name)
            tok = (m or {}).get("tokens") or {}
            tokens_in += int(tok.get("prompt") or tok.get("input") or 0)
            tokens_out += int(tok.get("candidates") or 0)
        name = used[0] if len(used) == 1 else (model or "default")
        return Completion(text=str(data["response"]), model=f"gemini_cli/{name}", duration=0.0,
                          input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=None)

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
        if time.time() < self.limited_until:
            info["limit"] = self.limit_message
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


class APIBackend:
    """An OpenAI-compatible HTTP API with its own key and a list of models to try in
    order (free-tier model line-ups change often; a vanished model just moves us on)."""

    LIMIT_RECHECK = 900.0

    def __init__(self, name: str, url: str, key_env: str, models: list[str], timeout: float,
                 json_mode: bool = False, transport=None, extra_headers: dict | None = None):
        self.name, self.url, self.key_env, self.models = name, url, key_env, models
        self.timeout, self.json_mode, self.transport = timeout, json_mode, transport
        self.extra_headers = extra_headers or {}
        self.limited_until = 0.0
        self.limit_message = ""
        self._client: GatewayLLM | None = None
        self._client_key = ""

    @property
    def key(self) -> str:
        return os.environ.get(self.key_env, "").strip() if self.key_env else ""

    @property
    def configured(self) -> bool:
        return bool(self.key) or not self.key_env

    def _gateway(self) -> GatewayLLM:
        if self._client is None or self._client_key != self.key:  # key added/changed at runtime
            self._client = GatewayLLM(self.url, self.key, self.timeout, self.transport, self.extra_headers)
            self._client_key = self.key
        return self._client

    async def complete(self, model: str | None, system: str, user: str, *, json_mode: bool = False) -> Completion:
        if not self.configured:
            raise LLMError(f"{self.name}: no API key ({self.key_env} not set)")
        if time.time() < self.limited_until:
            raise LLMError(f"{self.name}: rate limit — {self.limit_message}")
        errors = []
        for m in ([model] if model else self.models):
            try:
                comp = await self._gateway().complete(m, system, user, json_mode=json_mode and self.json_mode)
                comp.model = f"{self.name}/{m}"
                comp.cost_usd = comp.cost_usd if comp.cost_usd is not None else 0.0
                return comp
            except LLMError as exc:
                errors.append(str(exc))
                if exc.status == 429:
                    self.limited_until = time.time() + self.LIMIT_RECHECK
                    self.limit_message = str(exc)[-160:]
                    break
                if exc.status in (401, 403):
                    break  # bad key: other models won't help
        raise LLMError(f"{self.name}: " + " | ".join(errors))

    def status(self) -> dict:
        info = {"type": "api", "configured": self.configured, "key_env": self.key_env, "models": self.models}
        if time.time() < self.limited_until:
            info["limit"] = self.limit_message
        return info

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()


class RouterLLM:
    """Sends each alias to the backend configured in configs/providers.yaml, and when that
    backend fails (limit reached, not installed, no key…) walks the global `fallback` list."""

    def __init__(self, backends: dict, routes: dict[str, Route], default: Route, fallback: list[str] | None = None):
        self.backends = backends
        self.routes = routes
        self.default = default
        self.fallback = fallback or []

    @classmethod
    def from_config(cls, path: Path, gateway_url: str, gateway_key: str, timeout: float,
                    transport=None) -> "RouterLLM":
        data = load_yaml(path)
        backends: dict = {}
        for name, cfg in (data.get("backends") or {"gateway": {"type": "gateway"}}).items():
            kind = cfg.get("type", name)
            if kind in CLI_KINDS:
                backends[name] = CLIBackend(
                    kind=kind, command=cfg.get("command", DEFAULT_COMMAND[kind]),
                    default_model=cfg.get("model"), timeout=float(cfg.get("timeout", timeout)),
                    use_subscription=bool(cfg.get("use_subscription", True)),
                    extra_args=[str(a) for a in cfg.get("extra_args", [])])
            elif kind == "gateway":
                backends[name] = GatewayLLM(cfg.get("url", gateway_url).rstrip("/"),
                                            os.environ.get(cfg["key_env"], "") if cfg.get("key_env") else gateway_key,
                                            timeout)
            elif kind == "api":
                models = cfg.get("models") or ([cfg["model"]] if cfg.get("model") else [])
                if not cfg.get("url") or not models:
                    raise ValueError(f"backend '{name}' needs url and models")
                backends[name] = APIBackend(name, cfg["url"], cfg.get("key_env", ""), [str(m) for m in models],
                                            float(cfg.get("timeout", timeout)), bool(cfg.get("json_mode", False)),
                                            transport, cfg.get("headers"))
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
        fallback = [str(b) for b in (data.get("fallback") or [])]
        for route in [default, *routes.values()]:
            if route.backend not in backends:
                raise ValueError(f"providers.yaml routes to unknown backend '{route.backend}'")
        for b in fallback:
            if b not in backends:
                raise ValueError(f"providers.yaml fallback lists unknown backend '{b}'")
        return cls(backends, routes, default, fallback)

    def route_for(self, alias: str) -> Route:
        return self.routes.get(alias, self.default)

    async def _call(self, name: str, model: str | None, alias: str, system: str, user: str, json_mode: bool):
        backend = self.backends[name]
        if isinstance(backend, (CLIBackend, APIBackend)):
            return await backend.complete(model, system, user, json_mode=json_mode)
        # gateway / mock: the gateway maps the alias (or an explicit model) itself
        return await backend.complete(model or alias, system, user, json_mode=json_mode)

    async def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> Completion:
        route = self.route_for(model)
        errors: list[str] = []
        chain = [(route.backend, route.model)] + [(b, None) for b in self.fallback if b != route.backend]
        for name, backend_model in chain:
            backend = self.backends[name]
            if isinstance(backend, APIBackend) and not backend.configured:
                continue  # no key saved for this free API: skip silently
            try:
                return await self._call(name, backend_model, model, system, user, json_mode)
            except LLMError as exc:
                errors.append(str(exc)[:300])
        raise LLMError(" ; ".join(errors) or f"{model}: no backend available")

    async def status(self) -> dict:
        backends = {}
        for name, backend in self.backends.items():
            if isinstance(backend, CLIBackend):
                backends[name] = await backend.status()
            elif isinstance(backend, APIBackend):
                backends[name] = backend.status()
            else:
                backends[name] = {"type": "gateway" if isinstance(backend, GatewayLLM) else "mock"}
        return {"default": self.default.__dict__, "fallback": self.fallback,
                "aliases": {a: r.__dict__ for a, r in self.routes.items()},
                "backends": backends}

    async def aclose(self) -> None:
        for backend in self.backends.values():
            if hasattr(backend, "aclose"):
                await backend.aclose()
