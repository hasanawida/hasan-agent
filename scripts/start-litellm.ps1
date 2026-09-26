# Starts the LiteLLM gateway that Hassan AI OS talks to in live mode.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path "configs\litellm.yaml")) {
  Copy-Item "configs\litellm.example.yaml" "configs\litellm.yaml"
  Write-Host "Created configs\litellm.yaml - edit model IDs, then set OPENAI_API_KEY / ANTHROPIC_API_KEY / XAI_API_KEY."
}
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q "litellm[proxy]"
& .\.venv\Scripts\litellm.exe --config configs\litellm.yaml --port 4000
