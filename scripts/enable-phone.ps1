# Lets your phone reach Hassan AI OS from anywhere, privately, over Tailscale.
#  * Tailscale builds an encrypted private network between YOUR devices only.
#  * `tailscale serve` publishes http://127.0.0.1:8787 as https://<pc>.<tailnet>.ts.net
#    inside that private network (NOT on the public internet), with a real HTTPS
#    certificate so the microphone works on the phone.
# Then scan the QR code in the dashboard's "phone" card.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $p) { return $p }
    return $null
}

$ts = Find-Tailscale
if (-not $ts) {
    Write-Host "Installing Tailscale..." -ForegroundColor Cyan
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
    } else {
        Start-Process "https://tailscale.com/download/windows"
        Read-Host "Install Tailscale from the page that opened, then press Enter"
    }
    $ts = Find-Tailscale
    if (-not $ts) { throw "Tailscale not found after install. Re-run this script." }
}

# Log in (opens the browser the first time)
$state = (& $ts status --json | ConvertFrom-Json)
if ($state.BackendState -ne "Running") {
    Write-Host "Log in to Tailscale in the browser window..." -ForegroundColor Cyan
    & $ts up
    $state = (& $ts status --json | ConvertFrom-Json)
}
$dns = $state.Self.DNSName.TrimEnd(".")
if (-not $dns) { throw "Could not read this PC's Tailscale name. Is MagicDNS enabled in the Tailscale admin console?" }

$port = 8787
$envFile = Join-Path $Root "hassan.env"
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern "^HASSAN_PORT=(\d+)" | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

Write-Host "Publishing Hassan AI OS to your private tailnet (HTTPS)..." -ForegroundColor Cyan
Write-Host "If Tailscale asks you to enable HTTPS certificates, open the link it prints and approve."
& $ts serve --bg $port
if ($LASTEXITCODE -ne 0) { throw "tailscale serve failed" }

# Save the phone URL for the dashboard's QR code
$url = "https://$dns"
$lines = @()
if (Test-Path $envFile) { $lines = Get-Content $envFile | Where-Object { $_ -notmatch "^HASSAN_PUBLIC_URL=" } }
$lines += "HASSAN_PUBLIC_URL=$url"
$lines | Set-Content -Path $envFile -Encoding UTF8

# Restart Hassan so it picks the URL up
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $py) {
    & $py -m hassan_ai stop | Out-Null
    & $py -m hassan_ai open
}
Write-Host ""
Write-Host "Done. Phone URL: $url" -ForegroundColor Green
Write-Host "1) Install Tailscale on the phone and log in with the SAME account."
Write-Host "2) Scan the QR code in the dashboard card (phone)."
Write-Host "3) Browser menu -> Add to Home screen."
Write-Host "To turn it off:  tailscale serve reset"
