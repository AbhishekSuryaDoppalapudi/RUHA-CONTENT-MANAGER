"""Static settings (paths, limits) and the runtime configuration defaults.

Static settings come from environment variables / ``.env``; runtime settings (models,
quality, subtitles...) live in the ``model_settings`` table and are edited from the UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FONT_EXTENSIONS = {".ttf", ".otf"}


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    data_dir: Path
    max_upload_bytes: int = 20 * 1024**3
    host: str = "127.0.0.1"
    port: int = 8765
    translator_dir: Path | None = None
    translator_python: Path | None = None
    start_worker: bool = True
    cors_origins: tuple = ("http://localhost:5173", "http://127.0.0.1:5173")

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).resolve()

    # ---- derived locations -------------------------------------------------
    @property
    def db_path(self) -> Path: return self.data_dir / "ruha.db"
    @property
    def projects_dir(self) -> Path: return self.data_dir / "projects"
    @property
    def chroma_dir(self) -> Path: return self.data_dir / "chroma"
    @property
    def brand_dir(self) -> Path: return self.data_dir / "brand"
    @property
    def backups_dir(self) -> Path: return self.data_dir / "backups"
    @property
    def tmp_dir(self) -> Path: return self.data_dir / "tmp"
    @property
    def logs_dir(self) -> Path: return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.projects_dir, self.chroma_dir, self.brand_dir,
                  self.backups_dir, self.tmp_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def allowed_roots(self) -> list:
        return [self.data_dir]


def load_settings(data_dir=None, **overrides) -> Settings:
    _load_dotenv()
    # default is separate from the legacy Streamlit MVP's "storage" folder so the two never mix
    data = Path(data_dir or os.getenv("RUHA_DATA_DIR") or ROOT / "ruha_data")
    tdir = os.getenv("RUHA_TRANSLATOR_DIR", r"C:\Dev\indic-video-translator")
    tpy = os.getenv("RUHA_TRANSLATOR_PYTHON") or str(Path(tdir) / ".venv" / "Scripts" / "python.exe")
    s = Settings(
        data_dir=data,
        max_upload_bytes=int(float(os.getenv("RUHA_MAX_UPLOAD_GB", "20")) * 1024**3),
        host=os.getenv("RUHA_HOST", "127.0.0.1"),
        port=int(os.getenv("RUHA_PORT", "8765")),
        translator_dir=Path(tdir) if tdir else None,
        translator_python=Path(tpy) if tpy else None,
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    s.ensure_dirs()
    return s


# ---------------------------------------------------------------------------
# Runtime configuration defaults (stored in model_settings: key -> category/value)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    "whisper.model": {"category": "ai", "value": "small"},
    "whisper.device": {"category": "ai", "value": "auto"},          # auto | cuda | cpu
    "whisper.compute_type": {"category": "ai", "value": "auto"},
    "llm.model": {"category": "ai", "value": "qwen2.5:3b"},
    "llm.base_url": {"category": "ai", "value": "http://localhost:11434"},
    "llm.keep_alive": {"category": "ai", "value": "2m"},
    "llm.temperature": {"category": "ai", "value": 0.35},
    "embedding.provider": {"category": "ai", "value": "auto"},      # auto | ollama | hash
    "embedding.model": {"category": "ai", "value": "nomic-embed-text"},
    "gpu.mode": {"category": "gpu", "value": "auto"},               # auto | gpu | cpu
    "gpu.min_free_ram_gb": {"category": "gpu", "value": 3.0},
    "render.quality": {"category": "export", "value": "balanced"},  # draft | balanced | high
    "render.width": {"category": "export", "value": 1080},
    "render.height": {"category": "export", "value": 1920},
    "render.burn_subtitles": {"category": "export", "value": True},
    "render.apply_watermark": {"category": "export", "value": True},
    "render.apply_intro_outro": {"category": "export", "value": True},
    "subtitle.style": {"category": "subtitles", "value": {
        "creative": True, "font": "Segoe UI Black", "indic_font": "Nirmala UI", "size": 78, "bold": True,
        "primary": "#FFFFFF", "outline_color": "#000000", "outline": 4, "margin_v": 300,
        "max_chars_per_line": 24}},
    "subtitle.language": {"category": "subtitles", "value": "English"},   # reel subtitle language (default English)
    "reel.music_path": {"category": "export", "value": ""},                 # optional music for image reels
    "reel.hook_card": {"category": "export", "value": True},               # opening hook image
    "reel.closing_card": {"category": "export", "value": True},            # closing image
    "creative.backend": {"category": "creative", "value": "auto"},           # auto | diffusers | comfyui | none
    "creative.model": {"category": "creative", "value": "auto"},             # auto | sd15 | sdxl | sdxl-turbo
    "creative.comfyui_url": {"category": "creative", "value": "http://127.0.0.1:8188"},
    "creative.comfy_checkpoint": {"category": "creative", "value": ""},
    "creative.steps": {"category": "creative", "value": 0},                  # 0 = model default
    "creative.allow_cpu": {"category": "creative", "value": False},          # very slow; off unless you want it
    "creative.offload": {"category": "creative", "value": "auto"},
    "creative.max_attempts": {"category": "creative", "value": 2},
    "creative.show_brand": {"category": "creative", "value": True},
    "creative.timeout_s": {"category": "creative", "value": 900},
    "creative.benchmark": {"category": "creative", "value": {}},
    "design.engine": {"category": "design", "value": "html"},              # html (browser, best quality) | pillow
    "design.ai_background": {"category": "design", "value": False},        # needs NVIDIA GPU + requirements-gpu.txt
    "design.ai_model": {"category": "design", "value": "stabilityai/sdxl-turbo"},
    "design.theme_backgrounds": {"category": "design", "value": True},     # theme-matched poster/post backgrounds
    "queue.paused": {"category": "queue", "value": False},
    "app.theme": {"category": "app", "value": "dark"},
    "app.owner_name": {"category": "app", "value": "Owner"},
    "privacy.allow_network": {"category": "privacy", "value": False},
    "logging.level": {"category": "logging", "value": "INFO"},
    "storage.warn_free_gb": {"category": "app", "value": 10},
}

QUALITY_CRF = {"draft": 28, "balanced": 23, "high": 18}
PROJECT_STATUSES = ["draft", "uploaded", "processing", "review_required", "approved",
                    "published", "failed", "archived"]
ASSET_STATUSES = ["draft", "approved", "rejected", "revision_requested"]
ASSET_KINDS = ["main_reel", "short_reel", "poster", "social_post", "subtitle", "transcript",
               "caption", "translation_video", "translation_subtitle", "audio", "video", "image_reel", "creative"]
JOB_TYPES = ["transcription", "translation", "dubbing", "reel_generation", "poster_generation",
             "social_post_generation", "video_rendering", "video_download", "image_reel",
             "creative_brief", "creative_generation", "creative_benchmark", "yt_narrate", "yt_video"]
JOB_STATUSES = ["queued", "running", "completed", "failed", "cancelled"]
CALENDAR_STATUSES = ["draft", "approved", "scheduled", "published"]

STARTER_TOPICS = [
    "Faith", "Hope", "Forgiveness", "Prayer", "Grace", "Love", "Patience", "Gratitude",
    "Courage", "Purpose", "Healing", "Family", "Generosity", "Humility", "Perseverance",
    "Peace", "Wisdom", "Obedience", "Community", "Renewal",
]
