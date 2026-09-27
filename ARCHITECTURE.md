# Hassan AI OS — Architecture (v0.3)

```text
User / Arabic Dashboard (hassan_ai/static/index.html)
          |
          v
+-----------------------------------+
| FastAPI Control Plane             |  hassan_ai/server.py
| Tasks / Events / Approvals / Undo |
+-----------------+-----------------+
                  |
                  v
+-----------------------------------+
| Hassan Orchestrator               |  hassan_ai/orchestrator.py
| Manager → Analyst → Planner →     |
| (Researcher ∥ Coder×N) →          |
| Reviewer (+Cross) → Judge →       |
| Approval → Apply → Verify →       |
| Repair loop → Decision            |
+--------+-----------------+--------+
         |                 |
  Model layer         Evidence + Execution          Memory (SQLite)
  hassan_ai/llm.py    hassan_ai/execution.py        hassan_ai/memory.py
  Gateway | Mock      ProjectDetector               tasks / events / approvals
  per-agent           SafeLocalRunner (allow-list)  project_memory
  fallback chain      CheckpointManager             model_stats (capability registry)
         |            ExecutionManager
   LiteLLM gateway          |
   (aliases → providers)    +---- Policy (hassan_ai/policy.py, policies/default.yaml)
                            |
                        MCP v2 bus (hassan_ai/mcp_bus.py)
                            |
          +-----------------+------------------+
          v                 v                  v
   Visual Studio MCP   Blender MCP    Hassan Local Project MCP
   (external)          (external)     connectors/local-project-mcp
```

## Design rules

1. **Evidence beats voting.** The Judge sees build/test/git/file evidence and picks a candidate plan; it is not a majority vote.
2. **No unrestricted shell.** `SafeLocalRunner` only runs argv lists produced by the project detector, with an executable allow-list, no `shell=True`.
3. **Model independence.** Agents reference gateway aliases; provider IDs live only in `configs/litellm.yaml`.
4. **Workspace containment.** Every path resolves inside the workspace (symlinks included); `.git` internals and secret files are refused; workspaces must be under `HASSAN_ALLOWED_ROOTS`.
5. **Human approval for mutations.** File changes are shown as a unified diff and applied only after approval; each repair round needs a new approval. Mutating MCP verbs are blocked at the public API.
6. **Recovery before mutation.** A checkpoint (HEAD + `git stash create` snapshot, which does not touch the working tree) is recorded before execution, every approved write backs the file up first, and `/rollback` restores them.
7. **Self-recovery, not refusal.** Each agent has a fallback chain of aliases; failures are logged as events and in `model_stats`, and only reported when every option is exhausted.
8. **Core works offline.** Mock mode exercises the full pipeline (including real execution) without keys.

## Task lifecycle

`queued → running → (awaiting_approval ⇄ running)* → completed | rejected | failed`

`TaskRecord` persists: resolved mode, agent outputs (model, timing, errors), plan, structured change plan, evidence, checkpoint, verification result, repair round and final decision. Tasks that were mid-run during a restart are marked failed on startup; tasks awaiting approval survive restarts.

## Modes

| Mode | Agents |
|---|---|
| fast | Manager, Planner, Coder, Decision |
| auto | Manager routes: low → fast, medium → full team (single coder), high → consensus |
| consensus | Full team, Coder runs once per alias in `consensus.coders` in parallel, extra cross-reviewer |

## Manual desktop control

`hassan_ai/desktop.py` adds a human-operated input channel alongside the existing FFmpeg screen stream. The dashboard locally grants access for 30 minutes; `/api/desktop/control` accepts one authenticated WebSocket with an exact Origin check (including scheme and port). Remote input requires HTTPS. HTTP middleware does not cover WebSockets, so the route independently checks the existing access key and proxy/local classification.

An allow-listed protocol covers pointer position, buttons, scrolling, keys and short text. One worker owns the Windows input devices and DPI context. The session releases held inputs on disconnect, grant expiry, key rotation, local revocation or inactivity. A watchdog closes connections without a heartbeat. No grant is persisted and no new model-facing tool is registered; Operator approval rules remain unchanged.

The UI reuses the screen card and paired-phone login. The input coordinates map to the entire virtual desktop, matching FFmpeg's `gdigrab desktop`, including negative monitor origins. Browser coordinates account for letterboxing in fullscreen. The input dependencies are `pynput` on Windows and `websockets` for Uvicorn; viewing still uses FFmpeg.

## Production evolution

- PostgreSQL + queue for multi-worker dispatch; durable workflows (Temporal) for restart-safe long tasks.
- OpenTelemetry traces across model calls, MCP tools and execution steps.
- Secrets manager instead of environment variables; AuthN/AuthZ before binding beyond localhost.
- Route by measured `model_stats` instead of static aliases.
