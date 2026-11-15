"""Download a video from a link (YouTube and other sites yt-dlp supports) for local processing.

Only use this for videos you own or have permission to use. The downloaded file is processed
entirely on this computer like any uploaded video."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


class DownloadError(Exception):
    pass


def validate_url(url: str) -> str:
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise DownloadError("Please paste a full link starting with http:// or https://")
    return url


def download(url: str, out_dir: Path, max_height: int = 720, progress=None) -> tuple[Path, str]:
    """Download into ``out_dir``; returns (file path, video title). ``progress(fraction)`` is optional."""
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloadError("yt-dlp is not installed. Run: pip install yt-dlp") from e
    url = validate_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook],
        "restrictfilenames": True,
        "socket_timeout": 30, "retries": 5,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            path = Path(y.prepare_filename(info))
    except Exception as e:                                  # yt-dlp raises many types
        msg = str(e).replace("ERROR: ", "")[:400]
        raise DownloadError(f"Could not download that link: {msg}") from e
    if not path.exists():
        cand = path.with_suffix(".mp4")
        if cand.exists():
            path = cand
        else:
            raise DownloadError("The download finished but the file could not be found")
    return path, (info.get("title") or path.stem)
