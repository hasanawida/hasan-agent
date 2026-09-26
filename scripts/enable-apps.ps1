# Lets Hassan's Operator control apps:
#  * Windows-MCP (open source): click / type / read the screen of ANY app (CapCut, browsers...)
#  * ffmpeg: webcam photo, microphone recording, video/audio editing
#  * checks Blender and VS Code
# Every click, typing, recording or command still waits for Hassan's approval.

$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$HasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

# 1. uv (runs Windows-MCP)
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Cyan
    if ($HasWinget) { winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements }
    else { powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" }
    Refresh-Path
}
if (Get-Command uvx -ErrorAction SilentlyContinue) {
    Write-Host "Preparing Windows-MCP (first run downloads it)..." -ForegroundColor Cyan
    uvx windows-mcp --help | Out-Null
    Write-Host "  [ok] Windows-MCP" -ForegroundColor Green
} else {
    Write-Host "  [--] uv not found; Windows app control stays off" -ForegroundColor Yellow
}

# 2. ffmpeg (camera, microphone, video editing)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing ffmpeg..." -ForegroundColor Cyan
    if ($HasWinget) { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements }
    else { Write-Host "Install ffmpeg from https://www.gyan.dev/ffmpeg/builds/ and add it to PATH" -ForegroundColor Yellow }
    Refresh-Path
}
foreach ($cli in @("ffmpeg", "code")) {
    if (Get-Command $cli -ErrorAction SilentlyContinue) { Write-Host "  [ok] $cli" -ForegroundColor Green }
    else { Write-Host "  [--] $cli not found" -ForegroundColor Yellow }
}
$blender = Get-ChildItem "C:\Program Files\Blender Foundation\Blender*\blender.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
if ($blender -or (Get-Command blender -ErrorAction SilentlyContinue)) { Write-Host "  [ok] blender" -ForegroundColor Green }
else { Write-Host "  [--] Blender not found (install from blender.org)" -ForegroundColor Yellow }

# 3. Turn on the Windows connector in hassan.env (local file, not in git)
$envFile = Join-Path $Root "hassan.env"
$lines = @()
if (Test-Path $envFile) { $lines = Get-Content $envFile | Where-Object { $_ -notmatch "^HASSAN_MCP_ENABLE=" } }
$lines += "HASSAN_MCP_ENABLE=windows"
$lines | Set-Content -Path $envFile -Encoding UTF8

# 4. Restart Hassan so the background server sees the new PATH and settings
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py -m pip install -q -e ".[mcp]"
& $py -m hassan_ai stop | Out-Null
& $py -m hassan_ai open
Write-Host ""
Write-Host "Done. In the dashboard choose 'control the PC' and try:" -ForegroundColor Green
Write-Host "  - take a screenshot"
Write-Host "  - open CapCut and tell me what is on the screen"
Write-Host "  - make a red cube in Blender and render it"
