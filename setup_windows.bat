@echo off
cd /d "%~dp0"
echo Checking Python...
py -3.11 --version
if errorlevel 1 (
  echo Python 3.11 was not found. Install Python 3.11 and enable Add Python to PATH.
  pause
  exit /b 1
)
echo Checking FFmpeg...
where ffmpeg
if errorlevel 1 (
  echo FFmpeg is not installed or not in PATH.
  echo Install FFmpeg, then reopen this terminal.
  pause
  exit /b 1
)
echo Checking Ollama...
where ollama
if errorlevel 1 (
  echo Ollama was not found. Install Ollama from https://ollama.com/
  pause
  exit /b 1
)
echo.
echo Pulling the default local model qwen2.5:3b...
ollama pull qwen2.5:3b
echo.
echo Setup checks completed. Run run.bat.
pause
