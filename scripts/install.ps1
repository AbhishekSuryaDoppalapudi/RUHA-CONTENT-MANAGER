# El Ruach Manager - Windows installer. Run via install.bat (or: powershell -ExecutionPolicy Bypass -File scripts\install.ps1)
# Creates .venv, installs Python packages, builds the web UI, and reports what else is needed.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "    [ok]   $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "    [warn] $m" -ForegroundColor Yellow }

# ---------- Python (3.11 - 3.13) ----------
Step 'Checking Python'
$py = $null
foreach ($v in '3.11', '3.12', '3.13') {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @('py', "-$v"); break } } catch {}
}
if (-not $py) {
    try { $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
          if ($ver -and [version]$ver -ge [version]'3.11') { $py = @('python') } } catch {}
}
if (-not $py) { Write-Host 'Python 3.11 or newer (64-bit) is required. Install it from python.org, then run this again.' -ForegroundColor Red; exit 1 }
$pyexe = $py[0]; $pyargs = @($py | Select-Object -Skip 1)
Ok ("Using: " + (& $pyexe @pyargs --version))

Step 'Creating virtual environment (.venv)'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & $pyexe @pyargs -m venv .venv }
$vpy = Join-Path $root '.venv\Scripts\python.exe'
& $vpy -m pip install --quiet --upgrade pip
Step 'Installing Python packages (first run downloads several hundred MB)'
& $vpy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed - see the message above.' -ForegroundColor Red; exit 1 }
& $vpy -c "import fastapi, sqlalchemy, chromadb, faster_whisper, PIL, psutil; print('imports ok')"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'A package could not be imported. If the message mentions "Application Control", Windows is blocking a compiled file;' -ForegroundColor Yellow
    Write-Host 'see docs/TROUBLESHOOTING.md ("Application Control policy has blocked this file").' -ForegroundColor Yellow
    exit 1
}
Ok 'Python packages installed'

# ---------- external tools ----------
Step 'Checking FFmpeg'
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Ok 'FFmpeg found' } else { Warn 'FFmpeg not found. Install:  winget install Gyan.FFmpeg   (then reopen this window)' }
Step 'Checking Ollama (local LLM)'
if (Get-Command ollama -ErrorAction SilentlyContinue) { Ok 'Ollama found - run pull_models.bat to download the default model' }
else { Warn 'Ollama not found. Install from https://ollama.com/download, then run pull_models.bat. Transcription works without it; content planning needs it.' }
Step 'Checking GPU'
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { Ok ((& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join '; ') } else { Warn 'No NVIDIA driver found - El Ruach will use the CPU (slower).' }

# ---------- Node (only to build the UI) ----------
Step 'Preparing Node.js for the web UI build'
$nodeDir = $null
$sys = Get-Command node -ErrorAction SilentlyContinue
if ($sys -and ([version](& node -p "process.versions.node")) -ge [version]'18.0') { Ok "System Node $(& node --version)" }
elseif (Test-Path 'tools\node\node.exe') { $nodeDir = (Resolve-Path 'tools\node').Path; Ok 'Using bundled tools\node' }
else {
    Warn 'Node.js not found - downloading a portable copy into tools\node (checksum verified)'
    $base = 'https://nodejs.org/dist/latest-v22.x'
    $sums = (Invoke-WebRequest -UseBasicParsing "$base/SHASUMS256.txt").Content -split "`n"
    $line = $sums | Where-Object { $_ -match 'node-v[\d\.]+-win-x64\.zip' } | Select-Object -First 1
    $name = ($line -split '\s+')[-1].Trim(); $sha = ($line -split '\s+')[0].Trim()
    New-Item -ItemType Directory -Force tools | Out-Null
    Invoke-WebRequest -UseBasicParsing "$base/$name" -OutFile "tools\$name"
    if ((Get-FileHash "tools\$name" -Algorithm SHA256).Hash.ToLower() -ne $sha) { Remove-Item "tools\$name"; Write-Host 'Node download failed checksum verification.' -ForegroundColor Red; exit 1 }
    Expand-Archive "tools\$name" -DestinationPath tools -Force; Remove-Item "tools\$name"
    Get-ChildItem tools -Directory | Where-Object { $_.Name -like 'node-v*' } | ForEach-Object { if (Test-Path 'tools\node') { Remove-Item -Recurse -Force 'tools\node' }; Rename-Item $_.FullName 'node' }
    $nodeDir = (Resolve-Path 'tools\node').Path
}
if ($nodeDir) { $env:PATH = "$nodeDir;$env:PATH" }

Step 'Building the web UI'
Push-Location frontend
& npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host 'npm install failed.' -ForegroundColor Red; exit 1 }
& npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host 'UI build failed.' -ForegroundColor Red; exit 1 }
Pop-Location
Ok 'UI built'

if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env'; Ok 'Created .env from .env.example' }
Step 'Self-check'
& $vpy -c "from ruha.config import load_settings; from ruha.context import AppContext; c = AppContext(load_settings(start_worker=False) if False else load_settings()); print('database schema version', c.db.schema_version()); c.db.dispose()"
Write-Host "`nInstallation complete. Start El Ruach Manager with start.bat" -ForegroundColor Green
