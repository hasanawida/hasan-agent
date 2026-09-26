# Runs the Hassan AI OS test suite.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -e ".[mcp,dev]"
& .\.venv\Scripts\python.exe -m pytest -q
