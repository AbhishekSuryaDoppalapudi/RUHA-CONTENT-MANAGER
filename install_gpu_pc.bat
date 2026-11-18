@echo off
rem One-click setup for the dedicated GPU PC: El Ruach Manager + the voice/translation engine (NVIDIA GPU build).
cd /d "%~dp0"
echo === Step 1 of 2: El Ruach Manager ===
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install.ps1"
if errorlevel 1 ( echo Step 1 failed. & pause & exit /b 1 )
echo.
echo === Step 2 of 2: voice and translation engine (GPU) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_translator_gpu.ps1"
if errorlevel 1 ( echo Step 2 failed. & pause & exit /b 1 )
echo.
echo All done. Double-click start.bat to open El Ruach Manager.
pause
