"""Download a video from a link (YouTube and other sites yt-dlp supports) for local processing.

Only use this for videos you own or have permission to use. The downloaded file is processed
entirely on this computer like any uploaded video."""
from __future__ import annotations

import shutil
import sys
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse


class DownloadError(Exception):
    pass


def validate_url(url: str) -> str:
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise DownloadError("Please paste a full link starting with http:// or https://")
    return url


VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def check_supported(url: str) -> None:
    """Clear, early errors for share links that cannot work (folders; everything else is tried)."""
    u, h = urlparse(url), host_of(url)
    if h.endswith("dropbox.com") and re.search(r"/(scl/fo|sh)/", u.path):
        raise DownloadError("That Dropbox link is a folder. Open the video itself, click Share > Copy link, and paste that link.")
    if h == "drive.google.com" and "/folders/" in u.path:
        raise DownloadError("That Google Drive link is a folder. Open the video itself, click Share > Copy link, and paste that link.")


def dropbox_direct(url: str) -> str:
    """A Dropbox share link as a direct-download link (dl=1)."""
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in ("dl", "raw")] + [("dl", "1")]
    return urlunparse(u._replace(query=urlencode(q)))


def _download_direct(url: str, out_dir: Path, progress=None) -> tuple[Path, str]:
    """Stream a direct file link (Dropbox) to ``out_dir``."""
    import requests
    try:
        r = requests.get(url, stream=True, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        raise DownloadError(f"Could not reach that link: {str(e)[:200]}") from e
    with r:
        if r.status_code >= 400:
            raise DownloadError(f"The link answered HTTP {r.status_code}. Make sure sharing is on for \"anyone with the link\".")
        ctype = r.headers.get("content-type", "").lower()
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", r.headers.get("content-disposition", ""), re.I)
        name = unquote(m.group(1)) if m else unquote(Path(urlparse(r.url).path).name) or "video.mp4"
        name = re.sub(r"[^\w.\- ]", "_", name)
        ext = Path(name).suffix.lower()
        if "text/html" in ctype or (ext and ext not in VIDEO_EXTS and not ctype.startswith(("video/", "application/octet-stream"))):
            raise DownloadError("That link is not a video file (it may need a sign-in). Share it with \"anyone with the link\" and try again.")
        if ext not in VIDEO_EXTS:
            name = Path(name).stem + ".mp4"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / name
        total, done = int(r.headers.get("content-length") or 0), 0
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    return dst, Path(name).stem


def _js_runtimes() -> dict:
    """YouTube needs a JavaScript runtime to solve its download challenges; without one, links often fail with
    HTTP 403. Prefer the deno installed next to this Python (pip install deno), then deno/node on PATH or in tools/."""
    here = Path(sys.executable).parent
    found: dict = {}
    for name in ("deno", "node"):
        cands = [here / f"{name}.exe", here / name, shutil.which(name)]
        cands += [p for p in (Path(__file__).resolve().parents[2] / "tools").glob(f"**/{name}.exe")]
        for c in cands:
            if c and Path(c).exists():
                found[name] = {"path": str(c)}
                break
    return found


# Tried in order; a refusal (403 / sign-in) on one YouTube client often works on another.
_CLIENT_VARIANTS = (None, ["tv", "web_safari"], ["android_vr"], ["mweb"])


def download(url: str, out_dir: Path, max_height: int = 1080, progress=None, log=None) -> tuple[Path, str]:
    """Download into ``out_dir``; returns (file path, video title). ``progress(fraction)`` is optional."""
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloadError("yt-dlp is not installed. Run: pip install yt-dlp") from e
    url = validate_url(url)
    check_supported(url)
    if host_of(url).endswith("dropbox.com"):                # yt-dlp cannot read Dropbox share pages reliably
        return _download_direct(dropbox_direct(url), out_dir, progress)
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
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    last = None
    for clients in _CLIENT_VARIANTS:
        attempt = dict(opts)
        if clients:
            attempt["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(attempt) as y:
                info = y.extract_info(url, download=True)
                path = Path(y.prepare_filename(info))
            last = None
            break
        except Exception as e:                              # yt-dlp raises many types
            last = e
            if log:
                log(f"Download attempt ({'default' if not clients else '/'.join(clients)}) failed: {str(e)[:200]}")
            low = str(e).lower()
            if not any(k in low for k in ("403", "forbidden", "sign in", "confirm you", "http error 429", "player")):
                break                                       # not a refusal: another client will not help
    if last is not None:
        msg = str(last).replace("ERROR: ", "")[:400]
        hint = ""
        if host_of(url) in ("drive.google.com", "docs.google.com") and any(k in msg.lower() for k in ("sign in", "permission", "access", "quota", "private")):
            hint = " Set the file's sharing to \"Anyone with the link\" in Google Drive, then try again."
        elif "403" in msg or "forbidden" in msg.lower():
            hint = (" YouTube refused the download. Try again in a few minutes, or download the video yourself and "
                    "upload the file instead.")
        raise DownloadError(f"Could not download that link: {msg}.{hint}") from last
    if not path.exists():
        cand = path.with_suffix(".mp4")
        if cand.exists():
            path = cand
        else:
            raise DownloadError("The download finished but the file could not be found")
    return path, (info.get("title") or path.stem)
