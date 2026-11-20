"""Generated-asset lifecycle: registration, versioning, edits and the approval workflow."""
from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path

from ruha.models import (Approval, AssetVersion, Feedback, GeneratedAsset, Poster, Reel, SocialPost, Subtitle,
                         utcnow)
from ruha.security import ensure_within

EDITABLE = {"title", "description", "caption", "hashtags"}
REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected", "revise": "revision_requested",
                  "comment": "comment"}
MIME_FALLBACK = {".srt": "application/x-subrip", ".md": "text/markdown", ".json": "application/json"}


class AssetError(ValueError):
    pass


def guess_mime(path) -> str:
    if not path:
        return "application/octet-stream"
    ext = Path(path).suffix.lower()
    return MIME_FALLBACK.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _snapshot(asset: GeneratedAsset, reason: str, actor: str = "owner") -> AssetVersion:
    return AssetVersion(asset_id=asset.id, version=asset.version, title=asset.title,
                        description=asset.description, caption=asset.caption,
                        hashtags_json=asset.hashtags_json, file_path=asset.file_path,
                        metadata_json=asset.metadata_json, status=asset.status, reason=reason,
                        created_by=actor)


def register(ctx, s, project_id: int, kind: str, file_path=None, *, slot=None, title="", description="",
             caption="", hashtags=None, video_id=None, job_id=None, meta=None, preview_path=None,
             index_memory: bool = False) -> GeneratedAsset:
    size = 0
    if file_path:
        file_path = ensure_within(file_path, ctx.settings.allowed_roots)
        size = file_path.stat().st_size if file_path.exists() else 0
    a = GeneratedAsset(project_id=project_id, video_id=video_id, kind=kind, slot=slot, title=title,
                       description=description, caption=caption,
                       hashtags_json=json.dumps(hashtags or [], ensure_ascii=False),
                       file_path=str(file_path) if file_path else None,
                       preview_path=str(preview_path) if preview_path else None,
                       mime=guess_mime(file_path) if file_path else None, size_bytes=size, job_id=job_id,
                       metadata_json=json.dumps(meta or {}, ensure_ascii=False, default=str))
    s.add(a)
    s.flush()
    s.add(_snapshot(a, "created"))
    return a


def replace_file(ctx, s, asset: GeneratedAsset, new_path, reason: str, *, fields: dict | None = None,
                 meta: dict | None = None) -> GeneratedAsset:
    """Install a regenerated file as a new version, archiving the previous file in ``versions/``."""
    new_path = ensure_within(new_path, ctx.settings.allowed_roots)
    old = Path(asset.file_path) if asset.file_path else None
    if old and old.exists() and old.resolve() != new_path.resolve():
        vdir = old.parent.parent / "versions" / str(asset.id)
        vdir.mkdir(parents=True, exist_ok=True)
        archived = vdir / f"v{asset.version}_{old.name}"
        shutil.move(str(old), archived)
        for v in s.query(AssetVersion).filter_by(asset_id=asset.id, version=asset.version):
            v.file_path = str(archived)
    for k, v in (fields or {}).items():
        if k == "hashtags":
            asset.hashtags_json = json.dumps(v, ensure_ascii=False)
        elif k in EDITABLE:
            setattr(asset, k, v)
    if meta is not None:
        asset.metadata_json = json.dumps(meta, ensure_ascii=False, default=str)
    asset.file_path = str(new_path)
    asset.size_bytes = new_path.stat().st_size if new_path.exists() else 0
    asset.mime = guess_mime(new_path)
    asset.version += 1
    asset.status = "draft"
    asset.updated_at = utcnow()
    s.flush()
    s.add(_snapshot(asset, reason))
    return asset


def edit(ctx, s, asset: GeneratedAsset, fields: dict, actor: str = "owner") -> GeneratedAsset:
    bad = set(fields) - EDITABLE
    if bad:
        raise AssetError(f"Cannot edit: {', '.join(sorted(bad))}")
    changed = {}
    for k, v in fields.items():
        if k == "hashtags":
            v = [h if h.startswith("#") else f"#{h}" for h in (str(x).strip().replace(" ", "") for x in v) if h]
            if v != asset.hashtags:
                asset.hashtags_json = json.dumps(v, ensure_ascii=False)
                changed[k] = v
        elif getattr(asset, k) != v:
            setattr(asset, k, v)
            changed[k] = v
    if not changed:
        return asset
    asset.version += 1
    if asset.status in ("approved", "rejected"):
        asset.status = "draft"           # a changed asset must be reviewed again
    asset.updated_at = utcnow()
    s.flush()
    s.add(_snapshot(asset, f"edited {', '.join(changed)}", actor))
    ctx.audit(s, "asset", asset.id, "edit", fields=list(changed))
    return asset


def review(ctx, s, asset: GeneratedAsset, action: str, reason: str = "", comment: str = "",
           actor: str = "owner") -> Approval:
    if action not in REVIEW_ACTIONS:
        raise AssetError("action must be approve, reject, revise or comment")
    if action in ("reject", "revise") and not (reason or "").strip():
        raise AssetError("A reason is required to reject or request a revision")
    result = REVIEW_ACTIONS[action]
    ap = Approval(asset_id=asset.id, asset_version=asset.version, action=result, actor=actor,
                  reason=reason or "", comment=comment or "")
    s.add(ap)
    snapshot_text = "\n".join(x for x in [asset.title, asset.caption, asset.description] if x)
    if action != "comment":
        asset.status = result
        asset.updated_at = utcnow()
        kind = {"approve": "positive", "reject": "negative", "revise": "revision"}[action]
        s.add(Feedback(project_id=asset.project_id, asset_id=asset.id, kind=kind,
                       note=reason or comment or "", snapshot=snapshot_text))
        ctx.audit(s, "asset", asset.id, f"review:{result}", reason=reason, version=asset.version)
    elif comment:
        s.add(Feedback(project_id=asset.project_id, asset_id=asset.id, kind="note", note=comment,
                       snapshot=snapshot_text))
    s.flush()
    return ap


TRANSLATABLE_KINDS = ("main_reel", "short_reel", "video", "image_reel")


def reel_source(s, project_id: int, asset_id) -> tuple:
    """(file path, title) of a finished reel/video that a translation or dub job should use instead of the sermon.
    ValueError (a permanent error, not retried) when it is not a usable reel of this project."""
    try:
        a = s.get(GeneratedAsset, int(asset_id))
    except (TypeError, ValueError):
        a = None
    if a is None or a.project_id != project_id:
        raise ValueError("That reel does not belong to this project.")
    if a.kind not in TRANSLATABLE_KINDS:
        raise ValueError("Only a reel or video can be translated or dubbed this way.")
    if not a.file_path or not Path(a.file_path).exists():
        raise ValueError("That reel has no video file yet (render it first).")
    return Path(a.file_path), a.title or a.slot or f"asset {a.id}"


def delete_files(ctx, asset: GeneratedAsset) -> int:
    """Remove the asset's files (current + archived versions) from disk. Returns files removed."""
    removed = 0
    paths = {asset.file_path, asset.preview_path} | {v.file_path for v in asset.versions}
    paths |= {x.get("file") for x in (asset.meta.get("variants") or {}).values() if isinstance(x, dict)}   # other formats
    for p in paths:
        if not p:
            continue
        try:
            path = ensure_within(p, ctx.settings.allowed_roots)
            if path.is_file():
                path.unlink()
                removed += 1
        except Exception:
            continue
    return removed


def public_meta(meta: dict) -> dict:
    """The asset's meta for the API: the other-format files are listed by format with their size, never by server path."""
    meta = dict(meta or {})
    if isinstance(meta.get("variants"), dict):
        meta["variants"] = {fid: {**{k: v for k, v in var.items() if k != "file"},
                                  "has_file": bool(var.get("file") and Path(var["file"]).exists())}
                            for fid, var in meta["variants"].items() if isinstance(var, dict)}
    return meta


def serialize(a: GeneratedAsset, s=None) -> dict:
    d = {"id": a.id, "project_id": a.project_id, "kind": a.kind, "slot": a.slot, "title": a.title,
         "description": a.description, "caption": a.caption, "hashtags": a.hashtags, "mime": a.mime,
         "size_bytes": a.size_bytes, "status": a.status, "version": a.version, "meta": public_meta(a.meta),
         "has_file": bool(a.file_path and Path(a.file_path).exists()),
         "has_preview": bool(a.preview_path and Path(a.preview_path).exists()),
         "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat(),
         "job_id": a.job_id}
    if s is not None:
        r = s.query(Reel).filter_by(asset_id=a.id).first()
        if r:
            d["reel"] = {"category": r.category, "start": r.start_s, "end": r.end_s, "hook": r.hook,
                         "reason": r.reason, "excerpt": r.transcript_excerpt,
                         "subtitle_asset_id": r.subtitle_asset_id}
        p = s.query(Poster).filter_by(asset_id=a.id).first()
        if p:
            d["poster"] = {"headline": p.headline, "subheadline": p.subheadline, "quote": p.quote,
                           "theme": p.theme}
        sp = s.query(SocialPost).filter_by(asset_id=a.id).first()
        if sp:
            d["social_post"] = {"headline": sp.headline, "body": sp.body, "reference": sp.reference}
        last = s.query(Approval).filter(Approval.asset_id == a.id, Approval.action != "comment") \
            .order_by(Approval.id.desc()).first()
        if last:
            d["last_review"] = {"action": last.action, "actor": last.actor, "reason": last.reason,
                                "at": last.created_at.isoformat(), "version": last.asset_version}
    return d
