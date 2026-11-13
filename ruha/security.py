"""Filename sanitising, path confinement and upload validation helpers."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


class PathSecurityError(ValueError):
    pass


def sanitize_filename(name: str, default: str = "file", max_len: int = 120) -> str:
    """Return a safe file name (no separators, no reserved names, bounded length)."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]  # drop any directory part
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^\w\-. ]+", "_", stem, flags=re.UNICODE).strip(" ._")
    stem = re.sub(r"\s+", "_", stem)
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:10].lower()
    if stem.lower() in _WIN_RESERVED:
        stem = f"_{stem}"
    if not stem:
        stem = default
    stem = stem[: max_len - len(ext) - 1]
    return f"{stem}.{ext}" if ext else stem


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text or "project")[:max_len].strip("-") or "project"


def safe_join(base, *parts: str) -> Path:
    """Join ``parts`` onto ``base`` and refuse anything that escapes ``base``."""
    base = Path(base).resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise PathSecurityError("Path escapes the allowed directory")
    return target


def ensure_within(path, roots) -> Path:
    p = Path(path).resolve()
    for r in roots:
        r = Path(r).resolve()
        if p == r or r in p.parents:
            return p
    raise PathSecurityError("File is outside the configured storage directories")


def is_within(path, root) -> bool:
    try:
        ensure_within(path, [Path(root)])
        return True
    except PathSecurityError:
        return False


def safe_extract_names(names) -> None:
    """Validate archive member names (zip-slip protection)."""
    for n in names:
        norm = n.replace("\\", "/")
        p = Path(norm)
        if (norm.startswith("/") or p.is_absolute() or ".." in p.parts or (len(n) > 1 and n[1] == ":")):
            raise PathSecurityError(f"Unsafe path in archive: {n}")


def temp_file(directory, suffix: str = ".tmp") -> Path:
    """Create an empty temp file and close its handle (an open handle blocks deletion on Windows)."""
    import os
    import tempfile
    fd, name = tempfile.mkstemp(suffix=suffix, dir=directory)
    os.close(fd)
    return Path(name)


def redact(text: str) -> str:
    """Strip user-profile directories and tokens from text destined for logs/API errors."""
    text = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+", r"C:\\Users\\<user>", text or "")
    text = re.sub(r"(hf_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,})", "<token>", text)
    return text
