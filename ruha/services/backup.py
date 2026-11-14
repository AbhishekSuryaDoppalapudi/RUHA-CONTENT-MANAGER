"""Backup & restore of the complete application state (database, brand files, project files).

The vector store is *not* archived: every memory text lives in ``memory_references`` and the
vectors are rebuilt from it after a restore, which is smaller and immune to index corruption.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from ruha import __version__
from ruha.models import BrandSetting
from ruha.security import PathSecurityError, ensure_within, safe_extract_names

BACKUP_PREFIX = "ruha-backup-"


class BackupError(RuntimeError):
    pass


def list_backups(app) -> list:
    out = []
    for p in sorted(app.settings.backups_dir.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True):
        out.append({"name": p.name, "size_bytes": p.stat().st_size,
                    "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()})
    return out


def backup_path(app, name: str) -> Path:
    p = (app.settings.backups_dir / Path(name).name)
    ensure_within(p, [app.settings.backups_dir])
    if not p.exists() or not p.name.startswith(BACKUP_PREFIX):
        raise BackupError("Backup not found")
    return p


def create_backup(app, include_media: bool = True, label: str = "") -> Path:
    s = app.settings
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{BACKUP_PREFIX}{stamp}{'-' + label if label else ''}.zip"
    dest = s.backups_dir / name
    with tempfile.TemporaryDirectory(dir=s.tmp_dir) as td:
        snap = Path(td) / "ruha.db"
        src = sqlite3.connect(s.db_path)
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)                      # consistent snapshot even while the app is running
        src.close()
        dst.close()
        manifest = {"app": "El Ruach Manager", "version": __version__, "created_at": datetime.now().isoformat(),
                    "data_dir": str(s.data_dir), "include_media": include_media,
                    "schema_version": app.db.schema_version()}
        with zipfile.ZipFile(dest.with_suffix(".tmp"), "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, "ruha.db")
            for folder in ("brand", "projects"):
                base = s.data_dir / folder
                for f in base.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(s.data_dir).as_posix()
                    if not include_media and ("/source/" in f"/{rel}" or "/versions/" in f"/{rel}"
                                              or f.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".wav")):
                        continue
                    z.write(f, rel)
    dest.with_suffix(".tmp").replace(dest)
    return dest


def inspect_backup(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            safe_extract_names(z.namelist())
            if "manifest.json" not in z.namelist() or "ruha.db" not in z.namelist():
                raise BackupError("Not a El Ruach Manager backup (manifest or database missing)")
            return json.loads(z.read("manifest.json"))
    except zipfile.BadZipFile as e:
        raise BackupError("File is not a valid zip archive") from e
    except PathSecurityError as e:
        raise BackupError(str(e)) from e


def _rebase(db_path: Path, old: str, new: str):
    if not old or old == new:
        return
    conn = sqlite3.connect(db_path)
    try:
        cols = [("projects", "workspace_dir"), ("projects", "thumbnail_path"), ("source_videos", "path"),
                ("source_videos", "thumbnail_path"), ("generated_assets", "file_path"),
                ("generated_assets", "preview_path"), ("asset_versions", "file_path"), ("subtitles", "path")]
        for t, c in cols:
            conn.execute(f"UPDATE {t} SET {c} = ? || substr({c}, ?) WHERE {c} LIKE ? ESCAPE '\\'",
                         (new, len(old) + 1, old.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"))
        for key, val in conn.execute("SELECT key, value_json FROM brand_settings").fetchall():
            v = json.loads(val)
            if isinstance(v, str) and v.startswith(old):
                conn.execute("UPDATE brand_settings SET value_json=? WHERE key=?", (json.dumps(new + v[len(old):]), key))
        conn.commit()
    finally:
        conn.close()


def restore_backup(app, path: Path, wait_seconds: int = 120) -> dict:
    """Replace the live data with a backup. A safety backup is made first."""
    manifest = inspect_backup(path)
    if manifest.get("schema_version", 0) > app.db.schema_version():
        raise BackupError("Backup was made by a newer El Ruach Manager version; update the application first")
    s = app.settings
    app.maintenance.set()
    try:
        t0 = time.time()
        while app.queue.current_job is not None:
            if time.time() - t0 > wait_seconds:
                raise BackupError("A job is still running; cancel or wait for it, then retry the restore")
            time.sleep(0.5)
        safety = create_backup(app, include_media=False, label="pre-restore")
        app.memory.close()
        app.db.dispose()
        stage = Path(tempfile.mkdtemp(dir=s.tmp_dir, prefix="restore_"))
        try:
            with zipfile.ZipFile(path) as z:
                z.extractall(stage)
            for ext in ("", "-wal", "-shm"):
                Path(str(s.db_path) + ext).unlink(missing_ok=True)
            shutil.copy2(stage / "ruha.db", s.db_path)
            for folder in ("brand", "projects"):
                target = s.data_dir / folder
                if (stage / folder).exists():
                    if manifest.get("include_media", True):
                        shutil.rmtree(target, ignore_errors=True)
                        shutil.move(str(stage / folder), target)
                    else:   # media-less backup: keep current media, overlay the rest
                        shutil.copytree(stage / folder, target, dirs_exist_ok=True)
                target.mkdir(parents=True, exist_ok=True)
            _rebase(s.db_path, manifest.get("data_dir", ""), str(s.data_dir))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        app.db.reopen()
        app.db.migrate()
        app.memory.close()
        shutil.rmtree(s.chroma_dir, ignore_errors=True)
        s.chroma_dir.mkdir(parents=True, exist_ok=True)
        rebuilt = app.memory.reindex()
        app.queue.recover_interrupted()
        return {"restored": path.name, "manifest": manifest, "safety_backup": safety.name,
                "memory_vectors_rebuilt": rebuilt}
    finally:
        app.maintenance.clear()
