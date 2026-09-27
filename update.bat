@echo off
:: Double-click: download the latest Hassan AI OS and restart it.
cd /d "%~dp0"
git pull || (echo git pull failed & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -q -e ".[mcp]"
.venv\Scripts\python.exe -m hassan_ai stop
.venv\Scripts\python.exe -m hassan_ai open
.venv\Scripts\python.exe -m hassan_ai status || (
  echo.
  echo *** Hassan did not come back up. Last log lines: ***
  powershell -NoProfile -Command "Get-Content data\server.log -Tail 20"
  echo.
  pause
  exit /b 1
)
echo Updated.
timeout /t 3 >nul
