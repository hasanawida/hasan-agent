# Ownership, checkpoints and verification

## Computer control

The server wires `operator.desktop = desktop` and
`desktop.on_takeover = orchestrator.take_over_desktop`.
An operator claims the computer before changing it and releases the claim when
its tool/process cleanup finishes. Read-only work does not claim manual input.
Another operator or a manual session blocks a new mutation.

Opening manual control requests cancellation of the owning task and waits for
that task's cleanup. The connection is ready only after the agent releases its
claim. A handoff that exceeds 30 seconds fails without overriding the owner.
Already completed changes remain; cancellation is not rollback and cannot undo
an operation already submitted to another application.

`GET /api/desktop` exposes:

- `connected`: a manual input connection is active.
- `owner_kind`: `manual`, `agent`, or null.
- `agent_task_id`: the owning task, when present.
- `handoff_pending`: manual control is waiting for cleanup.

The existing local grant, persistent-grant setting, origin validation and
heartbeat limits remain in force. WebSocket authentication uses the server's
browser authentication helper at connection time and on every message, so a
revoked browser session loses input access.

## Explicit continuation

Every operator action records progress in its task. Before execution it stores
`operator_pending`; after acknowledgement it stores the result and the action's
signature in `operator_completed`. A cancellation or crash during a mutation
leaves an uncertain action instead of assuming success or repeating it.

Restart recovery marks interrupted work failed and exposes its checkpoint. It
does not start the task again. `orchestrator.resume(task_id, resolution=None)`
creates a linked task with the same goal and phone target. It returns that same
child if the previous attempt's resume request is repeated. Continue the child
for a later attempt.

A continuation:

1. Reads the selected device again before making changes; a UI task cannot
   continue if a fresh UI observation is unavailable.
2. Rechecks saved file effects where supported.
3. Skips previously acknowledged mutations server-side, even if a model repeats
   the old action. Recent history and all acknowledged action signatures persist.
4. Requests fresh approvals for new changes. Earlier task-wide trust is not
   copied to the continuation.

If `resume_blocked` is true, inspect the device and resolve the pending action:
`completed` records that it happened and prevents replay; `not_completed` lets a
newly approved attempt proceed. The resolution itself executes nothing. Never
select a resolution merely to clear the warning. The parent/child link is saved
in one database transaction.

A phone mutation that times out without acknowledgement immediately ends the
attempt as incomplete. Later actions and automatic retries do not run. A known
handset failure is recorded as failed evidence, not successful execution.

## What verification means

Existing independent file-content and filesystem checks remain available.
Phone changes are followed by a fresh accessibility inspection; Windows UI
changes use an available, policy-approved Windows Snapshot tool. A further read
runs at the end of a task that changed the UI.

An action may include an outer `expect: {text_contains: "Saved"}` criterion.
The server checks that literal text against its independent readback. On phones
it compares only visible node text/descriptions, not package/class metadata.
A failed criterion or unavailable final UI read prevents a completed outcome.

A readable UI or successful tap does not prove the full user goal. UI tasks keep
`verified` unknown unless reporting a failed criterion, and the verification
summary states that limit. Screenshots are not being interpreted by a new vision
model here. Screens, custom canvases and protected apps that expose insufficient
accessibility data can remain unverifiable.

## Validation

The dedicated reliability suite has **14 passing tests** covering ownership,
manual handoff, durable intent, interrupted work, explicit resolution, no replay,
new approvals, file rechecks, incorrect UI criteria, missing final observation
and per-message browser revocation. An earlier combined run passed **69 tests**
across this work and the existing desktop, operator and reliability suites.
These tests use fake input and phone responses; they do not replace physical
Windows/Android interaction checks or performance benchmarks.
