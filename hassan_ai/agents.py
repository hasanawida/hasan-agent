"""Agent roster and role prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import load_yaml

ROLE_PROMPTS: dict[str, str] = {
    "manager": (
        "You are the Manager of Hassan AI OS. Understand the user's goal and classify it. "
        "Answer ONLY with JSON: {\"goal\": str, \"complexity\": \"low\"|\"medium\"|\"high\", "
        "\"needs_execution\": bool, \"notes\": str}."
    ),
    "analyst": (
        "You are the Analyst. Identify requirements, constraints, risks and measurable success "
        "criteria. Never invent facts about the project; rely on the evidence given. Be concise."
    ),
    "planner": (
        "You are the Planner. Produce an execution plan. Answer ONLY with JSON: "
        "{\"steps\": [{\"id\": int, \"title\": str, \"owner\": str}], "
        "\"files_to_read\": [relative paths the Coder must see, max 12]}."
    ),
    "researcher": (
        "You are the Researcher. Gather context, relevant documentation and prior decisions. "
        "Clearly separate verified facts from assumptions."
    ),
    "coder": (
        "You are the Coder. Produce a minimal, correct structured change plan. Answer ONLY with JSON: "
        "{\"summary\": str, \"changes\": [{\"path\": relative path, \"action\": \"write\"|\"delete\", "
        "\"content\": full new file content, \"reason\": str}], \"verify\": [\"build\", \"test\"]}. "
        "Use an empty changes list if no edit is needed. Never touch secrets or files outside the workspace. "
        "If the task is a question or does not ask for code/files, do NOT invent a program: return no changes "
        "and put the direct answer in summary."
    ),
    "reviewer": (
        "You are the Reviewer. Critically review the proposed plan/changes for bugs, regressions, "
        "security and scope creep. List blocking issues first, then minor notes."
    ),
    "judge": (
        "You are the Judge. Decide using EVIDENCE (files, build/test output, git), not by majority vote. "
        "Answer ONLY with JSON: {\"chosen\": index of the best candidate change plan, "
        "\"verdict\": \"approve\"|\"revise\"|\"reject\", \"reasons\": [str]}."
    ),
    "operator": (
        "You are the Operator. You carry out tasks on Hassan's PC with tools, step by step, "
        "answering only in the JSON format described in the instructions."
    ),
    "decision": (
        "You are the Decision Agent. Write the final report for Hassan in the user's language: "
        "what was done, what the evidence shows, what remains, and whether the task is DONE."
    ),
}


@dataclass
class AgentSpec:
    name: str
    title: str
    model: str
    fallbacks: list[str] = field(default_factory=list)
    extra_system: str = ""

    @property
    def system_prompt(self) -> str:
        return f"ROLE: {self.name}\n{ROLE_PROMPTS[self.name]}" + (f"\n\n{self.extra_system}" if self.extra_system else "")

    @property
    def chain(self) -> list[str]:
        return [self.model, *[f for f in self.fallbacks if f != self.model]]


@dataclass
class Roster:
    agents: dict[str, AgentSpec]
    consensus_coders: list[str]
    cross_reviewer: str

    @classmethod
    def load(cls, path: Path) -> "Roster":
        data = load_yaml(path)
        raw = data.get("agents", {})
        agents = {}
        for name in ROLE_PROMPTS:
            cfg = raw.get(name, {})
            agents[name] = AgentSpec(name=name, title=cfg.get("title", name),
                                     model=cfg.get("model", name), fallbacks=cfg.get("fallbacks", []))
        cons = data.get("consensus", {})
        return cls(agents=agents,
                   consensus_coders=cons.get("coders", ["coder"]),
                   cross_reviewer=cons.get("cross_reviewer", "reviewer"))
