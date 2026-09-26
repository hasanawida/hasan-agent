# Removes autostart, desktop shortcut and the `hassan` command. Your data/ folder is kept.
$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
& (Join-Path $Root ".venv\Scripts\python.exe") -m hassan_ai stop
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Hassan AI OS.lnk") -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "Hassan AI.lnk") -ErrorAction SilentlyContinue
$Bin = Join-Path $env:LOCALAPPDATA "HassanAI\bin"
Remove-Item $Bin -Recurse -ErrorAction SilentlyContinue
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath) {
    [Environment]::SetEnvironmentVariable("Path", (($UserPath -split ";") | Where-Object { $_ -and $_ -ne $Bin }) -join ";", "User")
}
Write-Host "Hassan AI OS autostart removed."
