# Installs the companion voice/translation engine (Whisper video translator) with the NVIDIA GPU build of PyTorch.
# El Ruach Manager uses it for narration (YT Video Making) and dubbing. Run via install_gpu_pc.bat.
param([string]$Dir = "C:\Dev\indic-video-translator")
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    [ok]   $m" -ForegroundColor Green }

Step 'Checking tools'
foreach ($t in 'git', 'ffmpeg') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
        Write-Host "$t not found. Install it (winget install Git.Git / Gyan.FFmpeg), reopen this window, run again." -ForegroundColor Red; exit 1 }
}
$py = $null
foreach ($v in '3.12', '3.11', '3.13') { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "-$v"; break } }
if (-not $py) { Write-Host 'Python 3.12 (64-bit) is required - install from python.org (tick Add to PATH).' -ForegroundColor Red; exit 1 }
Ok "Python $py"
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { Write-Host 'No NVIDIA driver found. Install the latest driver from nvidia.com/drivers first.' -ForegroundColor Red; exit 1 }
Ok ((& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join '; ')

Step "Getting the translator into $Dir"
if (Test-Path "$Dir\app.py") { Ok 'Already there - updating'; git -C $Dir pull --ff-only }
else { New-Item -ItemType Directory -Force (Split-Path $Dir) | Out-Null; git clone https://github.com/AbhishekSuryaDoppalapudi/Wispher.git $Dir }

Step 'Creating its virtual environment'
$vpy = Join-Path $Dir '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { & py $py -m venv "$Dir\.venv" }
& $vpy -m pip install --quiet --upgrade pip

Step 'Installing PyTorch (CUDA 12.1 build) - several GB, be patient'
& $vpy -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
if ($LASTEXITCODE -ne 0) { Write-Host 'PyTorch install failed.' -ForegroundColor Red; exit 1 }
Step 'Installing the remaining packages'
& $vpy -m pip install -r "$Dir\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed - see the message above.' -ForegroundColor Red; exit 1 }

Step 'Checking the GPU is visible to PyTorch'
$r = & $vpy -c "import torch; print(torch.cuda.is_available())"
if ($r -match 'True') { Ok 'CUDA is available - narration and dubbing will use the GPU' }
else { Write-Host 'CUDA is NOT available (it printed False). Update the NVIDIA driver and re-run; the app would fall back to the slow CPU.' -ForegroundColor Yellow }
Write-Host "`nDone. Now run start.bat in the El Ruach Manager folder." -ForegroundColor Green
