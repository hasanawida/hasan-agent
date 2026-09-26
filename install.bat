@echo off
:: Double-click: installs Hassan AI OS to start with Windows (no window) + desktop icon + `hassan` command.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1"
pause
