@echo off
cd /d "%~dp0"
echo Updating El Ruach Manager (code only - your data in storage\ is untouched)...
git pull --ff-only
if errorlevel 1 (
  echo git pull failed - resolve it manually, then run install.bat to refresh dependencies.
  pause
  exit /b 1
)
call install.bat
