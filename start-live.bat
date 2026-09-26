@echo off
:: Runs Hassan AI OS with real brains: Claude (Claude Code CLI) + ChatGPT (Codex CLI).
:: See configs\providers.yaml for which agent uses which brain.
setlocal
cd /d "%~dp0"
where claude >nul 2>nul || echo [!] claude not found. Install: npm i -g @anthropic-ai/claude-code  then run: claude   (log in once)
where codex  >nul 2>nul || echo [!] codex not found.  Install: npm i -g @openai/codex  then run: codex login   (Sign in with ChatGPT)
set HASSAN_AI_MODE=live
call "%~dp0start.bat"
