@echo off
rem Downloads the local models El Ruach uses (needs Ollama installed and internet access once).
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Get it from https://ollama.com/download and run this again.
  pause
  exit /b 1
)
echo Pulling the content-planning model qwen2.5:7b (about 4.7 GB; needs about 6 GB of free RAM or VRAM)...
ollama pull qwen2.5:7b
echo Pulling the small fallback planner qwen2.5:3b (about 2 GB; used when the 7b model is missing or does not fit)...
ollama pull qwen2.5:3b
echo Pulling the embedding model used for semantic memory search (about 270 MB)...
ollama pull nomic-embed-text
echo Done. Select models in El Ruach Manager - Processing Centre.
pause
