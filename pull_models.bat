@echo off
rem Downloads the local models El Ruach uses (needs Ollama installed and internet access once).
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Get it from https://ollama.com/download and run this again.
  pause
  exit /b 1
)
echo Pulling the default content-planning model (about 2 GB)...
ollama pull qwen2.5:3b
echo Pulling the embedding model used for semantic memory search (about 270 MB)...
ollama pull nomic-embed-text
echo Done. Select models in El Ruach Manager - Processing Centre.
pause
