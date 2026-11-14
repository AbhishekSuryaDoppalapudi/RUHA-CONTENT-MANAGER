@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo El Ruach Manager is not installed yet. Run install.bat first.
  pause
  exit /b 1
)
if not exist frontend\dist\index.html (
  echo The web UI has not been built. Run install.bat first.
  pause
  exit /b 1
)
set PORT=8765
for /f "tokens=2 delims==" %%a in ('findstr /b "RUHA_PORT=" .env 2^>nul') do set PORT=%%a
echo Starting El Ruach Manager on http://127.0.0.1:%PORT%  (close this window to stop it)
start "" "http://127.0.0.1:%PORT%"
.venv\Scripts\python.exe -m ruha.main
pause
