# Repeatable system reliability report

This report measures deterministic integration and UI-logic regressions with
fake input/phone backends. It does **not** measure model intelligence, competitor
rank, real device interaction, display fluidity, or Internet latency.

## Consume a completed run without rerunning tests

Capture the existing full run as pytest JUnit XML and Node TAP. From the repository
root in PowerShell:

```powershell
New-Item -ItemType Directory -Force data/quality | Out-Null
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest -q --junitxml=data/quality/pytest.xml
$nodeSuites = Get-ChildItem tests -Filter '*.cjs' | ForEach-Object { $_.FullName }
node --test --test-reporter=tap $nodeSuites > data/quality/node.tap
.\.venv\Scripts\python.exe scripts/quality-report.py --junit data/quality/pytest.xml --node-tap data/quality/node.tap
```

If another command already ran pytest, add its `--junitxml` option and consume that
artifact; do not run the suite twice merely to generate this report. Windows
PowerShell UTF-16 TAP output and UTF-8 output are both accepted.

Results are written to ignored `data/quality/latest.json`, `latest.md` (Arabic),
and timestamped JSON/Markdown snapshots. The JSON includes each testcase's actual
outcome/duration, source-artifact hashes and paths, version/commit, and the report
environment. Imported test artifacts do not prove where their tests ran, so that
uncertainty is explicit rather than assigning the report host to the test run.

## Explicitly run the selected regression package later

```powershell
.\.venv\Scripts\python.exe scripts/quality-report.py --run
```

`--run` invokes the named Python integration suites and all `tests/*.cjs` Node
suites. Pytest uses a new temporary directory outside the protected source tree.
This mode records the actual subprocess commands, exit codes and run environment.
The consume mode is the default and never invokes pytest or Node test execution.

## Reading the result

The Arabic report groups results into authentication/permissions, screen/input,
resume/cancellation/no-replay, phone control, and files/transfers. Remaining tests
appear as additional system checks. Groups classify tests; they are not claims
of exhaustive feature coverage.

The percentage is `passed / (passed + failed + errors + cancelled)`. Skipped and
TODO tests remain visible and are excluded from that denominator. A missing or
truncated result, inconsistent runner summary, or entirely missing required group
produces **incomplete**, with no percentage. A runner failure outside recorded
case failures also cannot produce a passing report.

The duration column is the sum of testcase durations, not wall-clock runtime or
interactive response time. Raw recorded suite durations are also retained in
JSON. Exit codes: `0` passed (possibly with disclosed skips), `1` test failures,
`2` missing/incomplete results or unfinished execution. These outputs are a
reproducible engineering baseline, not a claim that arbitrary user goals succeed.
