# Hassan AI OS - Windows installer
#  * creates .venv and installs Hassan AI OS
#  * writes hassan.env (live mode, allowed folders)
#  * adds the `hassan` command (usable from any terminal, including VS Code's)
#  * starts automatically at Windows login, without any window
#  * desktop shortcut "Hassan AI" that opens the dashboard
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
Write-Host "== Hassan AI OS installer ==" -ForegroundColor Cyan
Write-Host "Folder: $Root"

# 1. Python environment
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
$Py  = Join-Path $Root ".venv\Scripts\python.exe"
$PyW = Join-Path $Root ".venv\Scripts\pythonw.exe"
Write-Host "Installing packages (first time takes a minute)..."
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -e ".[mcp]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. Settings file (kept across updates; not committed to git)
$EnvFile = Join-Path $Root "hassan.env"
if (-not (Test-Path $EnvFile)) {
    @(
        "# Hassan AI OS settings. Edit and restart (hassan stop / hassan start).",
        "HASSAN_AI_MODE=live",
        "HASSAN_ALLOWED_ROOTS=$env:USERPROFILE",
        "HASSAN_PORT=8787"
    ) | Set-Content -Path $EnvFile -Encoding UTF8
    Write-Host "Created hassan.env"
}

# 3. `hassan` command on the user PATH (pip's launcher has the venv path baked in)
$Bin = Join-Path $env:LOCALAPPDATA "HassanAI\bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item (Join-Path $Root ".venv\Scripts\hassan.exe") (Join-Path $Bin "hassan.exe") -Force
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
if (($UserPath -split ";") -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable("Path", ($UserPath.TrimEnd(";") + ";" + $Bin).TrimStart(";"), "User")
    Write-Host "Added $Bin to your PATH (open a new terminal to use 'hassan')"
}

# 4. Shortcuts: autostart (hidden) + desktop
$Shell = New-Object -ComObject WScript.Shell
function New-Shortcut($Path, $Arguments, $Description) {
    $s = $Shell.CreateShortcut($Path)
    $s.TargetPath = $PyW
    $s.Arguments = $Arguments
    $s.WorkingDirectory = "$Root"
    $s.Description = $Description
    $s.IconLocation = "$env:SystemRoot\System32\shell32.dll,43"
    $s.WindowStyle = 7
    $s.Save()
}
$Startup = [Environment]::GetFolderPath("Startup")
New-Shortcut (Join-Path $Startup "Hassan AI OS.lnk") "-m hassan_ai serve" "Hassan AI OS background server"
$Desktop = [Environment]::GetFolderPath("Desktop")
New-Shortcut (Join-Path $Desktop "Hassan AI.lnk") "-m hassan_ai open" "Open Hassan AI OS"
Write-Host "Autostart + desktop shortcut created"

# 5. Check the brains
foreach ($cli in @("claude", "codex", "gemini", "code")) {
    if (Get-Command $cli -ErrorAction SilentlyContinue) { Write-Host "  [ok] $cli" -ForegroundColor Green }
    else { Write-Host "  [--] $cli not found" -ForegroundColor Yellow }
}

# 6. Start now and open the dashboard
& $Py -m hassan_ai stop | Out-Null
& $Py -m hassan_ai open
Write-Host ""
Write-Host "Done. Hassan AI OS now starts by itself when Windows starts." -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:8787   |   Desktop icon: 'Hassan AI'"
Write-Host 'From the VS Code terminal:  hassan ask "check this project"'
