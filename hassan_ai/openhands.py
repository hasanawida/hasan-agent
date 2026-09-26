"""OpenHands discovery adapter.

OpenHands stays an external runtime: Hassan keeps policy, memory, Judge and UI,
and can hand long coding episodes to an OpenHands Agent Server later. For now
we only discover what the server offers. Endpoint paths vary between releases,
so several known paths are probed and whatever answers is reported.
"""

from __future__ import annotations

import httpx

PROBES = {
    "health": ["/health", "/alive", "/api/health"],
    "agents": ["/api/agents", "/api/options/agents"],
    "models": ["/api/models", "/api/options/models"],
}


async def discover(base_url: str, timeout: float = 5.0) -> dict:
    report: dict = {"url": base_url, "reachable": False}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        for key, paths in PROBES.items():
            for path in paths:
                try:
                    resp = await client.get(path)
                except httpx.HTTPError as exc:
                    report.setdefault("errors", []).append(f"{path}: {exc.__class__.__name__}")
                    continue
                if resp.status_code < 400:
                    report["reachable"] = True
                    try:
                        report[key] = resp.json()
                    except ValueError:
                        report[key] = resp.text[:500]
                    break
    return report
