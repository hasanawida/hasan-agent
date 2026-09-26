@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo [Hassan AI OS] Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -e .
if "%HASSAN_AI_MODE%"=="" set HASSAN_AI_MODE=mock
if "%HASSAN_ALLOWED_ROOTS%"=="" set HASSAN_ALLOWED_ROOTS=%USERPROFILE%
echo [Hassan AI OS] Mode=%HASSAN_AI_MODE%  -  http://127.0.0.1:8787
start "" http://127.0.0.1:8787
python -m hassan_ai
