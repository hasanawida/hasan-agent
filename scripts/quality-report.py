#!/usr/bin/env python3
"""Report deterministic system reliability, never model intelligence or competitor rank.

By default consume existing pytest JUnit and Node TAP artifacts without rerunning tests.
Use --run explicitly to execute the selected fake-input regression suites first.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "auth": "المصادقة والصلاحيات",
    "input": "اللمس والشاشة وتسليم التحكّم",
    "resume": "الاستكمال والإلغاء وعدم تكرار الإجراءات",
    "phone": "التحكّم بالهاتف والإيجنت",
    "files": "الملفات والنقل والتحقق",
    "other": "اختبارات نظام إضافية",
}
SELECTED = (
    "tests/test_desktop.py", "tests/test_phone.py", "tests/test_phone_operator.py",
    "tests/test_operator_progress.py", "tests/test_reliability.py", "tests/test_operator.py",
    "tests/test_access_integration.py", "tests/test_browser_sessions.py", "tests/test_pairing_tickets.py",
    "tests/test_screen_capture.py", "tests/test_transfers.py",
)
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def read_artifact(path: Path) -> str:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Result artifact exceeds 32 MiB")
    data = path.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("Result artifact exceeds 32 MiB")
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return data.decode(encoding)


def finite_duration(value) -> float:
    number = float(value or 0)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid test duration")
    return number


def classify(source: str, name: str) -> str:
    value = (source + " " + name).lower()
    if any(word in value for word in ("auth", "token", "credential", "grant", "permission", "pairing",
                                      "pair_code", "origin", "revok", "https", "browser_sessions", "access_integration")):
        return "auth"
    if any(word in value for word in ("resume", "checkpoint", "restart", "uncertain", "replay", "cancelling",
                                      "cancellation", "intent_is_durable", "lease_loss", "unconfirmed")):
        return "resume"
    if any(word in value for word in ("transfer", "clipboard", "file", "copy", "unicode_write", "directory")):
        return "files"
    if "phone" in value:
        return "phone"
    if any(word in value for word in ("desktop", "screen", "monitor", "touch", "gesture", "finger", "takeover",
                                      "manual_owner", "pointer", "letterbox", "scroll")):
        return "input"
    return "other"


def parse_junit(path: Path) -> dict:
    text = read_artifact(path)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entity declarations are not accepted in test results")
    root = ET.fromstring(text)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Not a JUnit test report")
    cases = []
    for case in root.iter("testcase"):
        status = "passed"
        reason = None
        if case.find("error") is not None:
            status = "error"
        elif case.find("failure") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
            skipped = case.find("skipped")
            reason = (skipped.get("message") or "Skipped by test runner")[:240]
        source, name = case.get("classname", ""), case.get("name", "unnamed")
        cases.append({"name": name, "source": source, "runner": "pytest", "status": status,
                      "seconds": finite_duration(case.get("time", "0")), "group": classify(source, name),
                      **({"skip_reason": reason} if reason else {})})
    suites = [s for s in root.iter("testsuite") if not list(s.findall("testsuite"))]
    declared = sum(int(s.get("tests", "0")) for s in suites)
    if not cases or declared != len(cases):
        raise ValueError("Empty or incomplete JUnit report: declared testcase count does not match")
    recorded_failure = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
    actual_failure = sum(c["status"] in {"failed", "error"} for c in cases)
    if actual_failure != recorded_failure:
        raise ValueError("JUnit failure summary does not match testcases")
    return {"runner": "pytest", "path": str(path.resolve()), "complete": True, "cases": cases,
            "recorded_suite_seconds": sum(finite_duration(s.get("time", "0")) for s in suites),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def node_sources(root: Path = ROOT) -> dict:
    names = {}
    for path in sorted((root / "tests").glob("*.cjs")):
        for match in re.finditer(r"\btest\(\s*(['\"])(.*?)\1\s*,", path.read_text(encoding="utf-8")):
            names[match.group(2)] = path.relative_to(root).as_posix()
    return names


def parse_tap(path: Path, sources: dict | None = None) -> dict:
    text = read_artifact(path)
    if not re.search(r"^TAP version 13\s*$", text, re.MULTILINE) or re.search(r"^Bail out!", text, re.MULTILINE):
        raise ValueError("Invalid or aborted TAP report")
    sources = sources or {}
    summaries = {name: int(value) for name, value in re.findall(
        r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$", text, re.MULTILINE)}
    required = {"tests", "pass", "fail", "cancelled", "skipped", "todo"}
    if set(summaries) != required or not re.search(r"^1\.\.\d+\s*$", text, re.MULTILINE):
        raise ValueError("TAP report is truncated or lacks the Node test summary")
    duration = re.search(r"^# duration_ms ([\d.]+)\s*$", text, re.MULTILINE)
    if duration is None:
        raise ValueError("TAP report has no completed-run duration")
    cases = []
    pattern = re.compile(r"^\s*(not )?ok\s+\d+\s*-\s*(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        diagnostic = text[match.end():end]
        if re.search(r"^\s+type: ['\"]?suite", diagnostic, re.MULTILINE):
            continue
        name = match.group(2)
        directive = re.search(r"\s+#\s*(SKIP|TODO)\b(.*)$", name, re.IGNORECASE)
        status = "failed" if match.group(1) else "passed"
        extra = {}
        if directive:
            status = "skipped"
            extra["skip_reason"] = (directive.group(1) + directive.group(2))[:240]
            name = name[:directive.start()]
        if re.search(r"failureType: ['\"]?cancelledByParent", diagnostic):
            status = "cancelled"
        elapsed = re.search(r"^\s+duration_ms: ([\d.]+)\s*$", diagnostic, re.MULTILINE)
        source = sources.get(name, "Node test (source not in current checkout)")
        cases.append({"name": name, "source": source, "runner": "node", "status": status,
                      "seconds": finite_duration(elapsed.group(1)) / 1000 if elapsed else 0.0,
                      "group": classify(source, name), **extra})
    total = summaries["tests"]
    if total <= 0 or len(cases) != total or sum(summaries[k] for k in required - {"tests"}) != total:
        raise ValueError("TAP testcase count does not match the completed-run summary")
    counts = Counter(c["status"] for c in cases)
    if (counts["passed"] != summaries["pass"] or counts["failed"] != summaries["fail"]
            or counts["cancelled"] != summaries["cancelled"]
            or counts["skipped"] != summaries["skipped"] + summaries["todo"]):
        raise ValueError("TAP testcase outcomes do not match the Node summary")
    return {"runner": "node", "path": str(path.resolve()), "complete": True, "cases": cases,
            "recorded_suite_seconds": finite_duration(duration.group(1)) / 1000,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def collect(path: Path, parser, runner: str) -> dict:
    try:
        return parser(path)
    except (OSError, ValueError, UnicodeError, ET.ParseError) as exc:
        return {"runner": runner, "path": str(path.resolve()), "complete": False, "cases": [],
                "error": f"{type(exc).__name__}: {exc}"}


def version_command(args):
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=10, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def report_environment() -> dict:
    module = ast.parse((ROOT / "hassan_ai" / "__init__.py").read_text(encoding="utf-8"))
    version = next((ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)), None)
    changes = version_command(["git", "status", "--porcelain"])
    return {"os": platform.system(), "os_release": platform.release(), "machine": platform.machine(),
            "python": platform.python_version(), "node": version_command(["node", "--version"]),
            "app_version": version, "git_commit": version_command(["git", "rev-parse", "HEAD"]),
            "working_tree_modified": bool(changes) if changes is not None else None}


def totals(cases: list[dict]) -> dict:
    counts = Counter(case["status"] for case in cases)
    return {"total": len(cases), "passed": counts["passed"], "failed": counts["failed"],
            "errors": counts["error"], "cancelled": counts["cancelled"], "skipped": counts["skipped"],
            "sum_case_seconds": round(sum(c["seconds"] for c in cases), 6)}


def build_report(inputs: list[dict], environment: dict, execution: dict | None = None) -> dict:
    cases = [case for item in inputs for case in item["cases"]]
    overall = totals(cases)
    complete = len(inputs) == 2 and all(item["complete"] for item in inputs)
    failures = overall["failed"] + overall["errors"] + overall["cancelled"]
    runner_failure = bool(execution and any(run["returncode"] for run in execution.get("runs", [])))
    if runner_failure and not failures:
        complete = False  # a runner failure outside testcases cannot become a passing score
    grouped = {name: {"label_ar": label, **totals([c for c in cases if c["group"] == name])}
               for name, label in GROUPS.items()}
    missing_groups = [key for key in GROUPS if key != "other" and not grouped[key]["total"]]
    denominator = overall["passed"] + failures
    score = round(100 * overall["passed"] / denominator, 2) if complete and not missing_groups and denominator else None
    status = "incomplete" if not complete or missing_groups else "failed" if failures else "passed_with_skips" if overall["skipped"] else "passed"
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "deterministic_system_reliability", "status": status,
            "not_measured": ["LLM intelligence", "competitor rank", "physical phone/Windows interaction", "Internet latency"],
            "report_environment": environment,
            "test_environment": execution.get("environment") if execution else None,
            "test_environment_note": "Recorded by --run" if execution else "Not recorded by this report; report environment is not proof of test execution environment",
            "complete_inputs": complete, "missing_groups": missing_groups,
            "reliability_pass_percent": score, "score_denominator": "passed + failed + errors + cancelled; skips excluded",
            "totals": overall, "groups": grouped,
            "inputs": [{k: v for k, v in item.items() if k != "cases"} for item in inputs],
            "execution": execution, "cases": cases}


def markdown(report: dict) -> str:
    labels = {"passed": "نجحت الاختبارات المسجلة", "passed_with_skips": "نجحت الاختبارات المنفّذة مع اختبارات متجاوزة",
              "failed": "توجد اختبارات فاشلة", "incomplete": "النتائج غير مكتملة"}
    score = report["reliability_pass_percent"]
    lines = ["# تقرير موثوقية النظام", "", "هذا تقرير اختبارات حتمية للنظام، وليس تقييمًا لذكاء الإيجنت أو ترتيبًا أمام المنافسين.", "",
             f"الحالة: **{labels[report['status']]}**.",
             f"نسبة نجاح الاختبارات المنفّذة: **{score:g}%**." if score is not None else "نسبة النجاح غير محسوبة لأن ملفات النتائج أو تغطية المجموعات غير مكتملة.",
             "الاختبارات المتجاوزة مستثناة من النسبة وتظهر في الجدول؛ لا تعني النجاح.", "",
             "| المجموعة | نجح | فشل/خطأ/أُلغي | متجاوز | مجموع أزمنة الاختبارات (ثانية) |",
             "|---|---:|---:|---:|---:|"]
    for group in report["groups"].values():
        failed = group["failed"] + group["errors"] + group["cancelled"]
        lines.append(f"| {group['label_ar']} | {group['passed']} | {failed} | {group['skipped']} | {group['sum_case_seconds']:.3f} |")
    env = report["report_environment"]
    lines += ["", "الأزمنة أعلاه مجموع أزمنة الحالات المسجلة، وليست قياس سرعة الهاتف أو زمن الاستجابة على الإنترنت.", "",
              f"بيئة إنشاء التقرير: {env.get('os')} {env.get('os_release')}، Python {env.get('python')}، Node {env.get('node') or 'غير متاح'}.",
              f"نسخة المشروع: {env.get('app_version')}؛ commit: `{env.get('git_commit') or 'غير متاح'}`؛ تعديلات محلية: {env.get('working_tree_modified')}.",
              "بيئة تنفيذ الاختبارات مسجلة بواسطة هذا الأمر." if report["test_environment"] else "بيئة تنفيذ الاختبارات ليست مرفقة بملفات النتائج؛ بيانات البيئة السابقة تخص إنشاء هذا التقرير.", ""]
    if report["missing_groups"]:
        lines += ["مجموعات بلا نتائج مسجلة: " + "، ".join(GROUPS[k] for k in report["missing_groups"]) + ".", ""]
    problems = [item for item in report["inputs"] if not item["complete"]]
    if problems:
        lines += ["ملفات نتائج مفقودة أو غير مكتملة:", ""]
        lines += [f"- {item['runner']}: {item.get('error', 'غير مكتمل')}" for item in problems]
        lines.append("")
    failed = [case for case in report["cases"] if case["status"] in {"failed", "error", "cancelled"}]
    if failed:
        lines += ["اختبارات تحتاج مراجعة:", ""]
        lines += [f"- `{c['source']}::{c['name']}` — {c['status']}" for c in failed]
        lines.append("")
    skipped = [case for case in report["cases"] if case["status"] == "skipped"]
    if skipped:
        lines += ["الاختبارات المتجاوزة:", ""]
        lines += [f"- `{c['source']}::{c['name']}` — {c.get('skip_reason', 'لا يوجد سبب مسجل')}" for c in skipped]
        lines.append("")
    lines += ["الاختبارات تستخدم إدخالًا وهاتفًا وهميين. يلزم فحص الأجهزة الفعلية بصورة مستقلة؛ هذه النسبة لا تثبت اكتمال كل مهام المستخدم.", ""]
    return "\n".join(lines)


def run_selected(junit: Path, tap: Path, output: Path) -> dict:
    missing = [name for name in SELECTED if not (ROOT / name).is_file()]
    if missing:
        raise ValueError("Selected suite missing: " + ", ".join(missing))
    node_tests = sorted((ROOT / "tests").glob("*.cjs"))
    if not node_tests:
        raise ValueError("No Node regression suites found")
    env = {**os.environ, "PYTHONUTF8": "1"}
    base = tempfile.mkdtemp(prefix="hassan-quality-")  # outside the protected source tree
    commands = [[sys.executable, "-m", "pytest", *SELECTED, "-q", f"--basetemp={base}", f"--junitxml={junit}"],
                ["node", "--test", "--test-reporter=tap", *[str(p) for p in node_tests]]]
    execution = {"environment": report_environment(), "started_at": datetime.now(timezone.utc).isoformat(), "runs": []}
    for artifact in (junit, tap):
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("", encoding="utf-8")  # old successful results must not survive a failed launch
    for index, command in enumerate(commands):
        print("Running " + ("selected pytest reliability suites" if index == 0 else "Node fake-input suites"), flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=900, check=False)
        target = output / "pytest.log" if index == 0 else tap
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.stdout + ("\n" + result.stderr if result.stderr else ""), encoding="utf-8")
        execution["runs"].append({"command": command, "returncode": result.returncode})
    execution["finished_at"] = datetime.now(timezone.utc).isoformat()
    return execution


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, default=ROOT / "data/quality/pytest.xml")
    parser.add_argument("--node-tap", type=Path, default=ROOT / "data/quality/node.tap")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/quality")
    parser.add_argument("--run", action="store_true", help="Explicitly run selected fake-input suites before reporting")
    args = parser.parse_args(argv)
    args.junit, args.node_tap, args.output_dir = args.junit.resolve(), args.node_tap.resolve(), args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    execution = None
    if args.run:
        args.junit.parent.mkdir(parents=True, exist_ok=True)
        args.node_tap.parent.mkdir(parents=True, exist_ok=True)
        try:
            execution = run_selected(args.junit, args.node_tap, args.output_dir)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            # Never grade a stale artifact as this failed run's successful result.
            failure = {"schema_version": 1, "status": "incomplete", "reliability_pass_percent": None,
                       "error": f"{type(exc).__name__}: test execution did not finish"}
            (args.output_dir / "latest.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
            (args.output_dir / "latest.md").write_text("# تقرير موثوقية النظام\n\nلم يكتمل تشغيل الاختبارات؛ لا توجد نسبة نجاح لهذا التشغيل.\n", encoding="utf-8")
            print(failure["error"], file=sys.stderr)
            return 2
    inputs = [collect(args.junit, parse_junit, "pytest"),
              collect(args.node_tap, lambda path: parse_tap(path, node_sources()), "node")]
    report = build_report(inputs, report_environment(), execution)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    payload, human = json.dumps(report, ensure_ascii=False, indent=2), markdown(report)
    for stem in ("latest", "report-" + stamp):
        (args.output_dir / (stem + ".json")).write_text(payload, encoding="utf-8")
        (args.output_dir / (stem + ".md")).write_text(human, encoding="utf-8")
    print(json.dumps({"status": report["status"], "totals": report["totals"],
                      "reliability_pass_percent": report["reliability_pass_percent"],
                      "report": str(args.output_dir / "latest.md")}, ensure_ascii=False))
    return 2 if report["status"] == "incomplete" else 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
