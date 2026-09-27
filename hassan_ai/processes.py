"""Bounded subprocesses whose children are cleaned up on timeout/cancellation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import subprocess

import psutil


@dataclass
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


def terminate_tree(pid: int, *, created: float | None = None) -> None:
    """Stop only this process tree. Refuse stale PID files and report failures."""
    if pid <= 0 or pid == os.getpid():
        raise ValueError("Refusing an invalid process id")
    try:
        root = psutil.Process(pid)
        if created is not None and abs(root.create_time() - created) > .01:
            raise RuntimeError("Process id was reused; refusing to stop a different process")
    except psutil.NoSuchProcess:
        return
    stopped = []
    try:
        # Freeze parents before discovering their descendants so a shell cannot
        # keep launching children while cancellation walks the tree.
        pending = [root]
        seen = set()
        while pending:
            process = pending.pop()
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                process.suspend()
                stopped.append(process)
                pending.extend(process.children())
            except psutil.NoSuchProcess:
                pass
        for process in reversed(stopped):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(stopped, timeout=5)
        if any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in alive):
            raise RuntimeError("Some child processes did not stop")
    finally:
        # An access-denied error must not leave a surviving process suspended.
        for process in stopped:
            try:
                if process.is_running():
                    process.resume()
            except psutil.Error:
                pass


async def run_process(argv: list[str], *, timeout: float, input: bytes | None = None,
                      **options) -> ProcessResult:
    options.setdefault("stdout", asyncio.subprocess.PIPE)
    options.setdefault("stderr", asyncio.subprocess.PIPE)
    options.setdefault("stdin", asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL)
    if os.name == "nt":
        options.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    else:
        options.setdefault("start_new_session", True)
    launch = asyncio.create_task(asyncio.create_subprocess_exec(*argv, **options))
    try:
        proc = await asyncio.shield(launch)
    except asyncio.CancelledError:
        proc = await launch
        await finish_thread_call(terminate_tree, proc.pid)
        await proc.communicate(input)
        raise
    try:
        created = psutil.Process(proc.pid).create_time()
    except psutil.NoSuchProcess:
        created = None
    reader = asyncio.create_task(proc.communicate(input))
    timed_out = False
    try:
        out, err = await asyncio.wait_for(asyncio.shield(reader), timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
        try:
            await finish_thread_call(terminate_tree, proc.pid, created=created)
            out, err = await asyncio.wait_for(asyncio.shield(reader), 5)
        except BaseException:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            raise
        if isinstance(exc, asyncio.CancelledError):
            raise
        timed_out = True
    return ProcessResult(proc.returncode if proc.returncode is not None else -1, out or b"", err or b"", timed_out)


async def finish_thread_call(fn, *args, **kwargs):
    """Do not report a cancelled file operation while its worker still writes."""
    work = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await work
        finally:
            raise
