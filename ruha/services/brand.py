"""Brand identity (logo, colours, fonts, watermark, intro/outro) and reusable templates."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from ruha.config import FONT_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ruha.models import BrandSetting, Template
from ruha.security import sanitize_filename
from ruha.services.design import DEFAULT_BRAND

FILE_SLOTS = {"logo": IMAGE_EXTENSIONS, "watermark": IMAGE_EXTENSIONS, "intro": VIDEO_EXTENSIONS,
              "outro": VIDEO_EXTENSIONS, "font_heading": FONT_EXTENSIONS, "font_body": FONT_EXTENSIONS}
DEFAULT_EXPORT = {"reel": [1080, 1920], "poster": [1080, 1350], "social": [1080, 1350]}


class BrandError(ValueError):
    pass


def _get(s, key, default=None):
    row = s.get(BrandSetting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value_json)
    except ValueError:
        return default


def _put(s, key, value):
    row = s.get(BrandSetting, key)
    if row is None:
        s.add(BrandSetting(key=key, value_json=json.dumps(value, ensure_ascii=False)))
    else:
        row.value_json = json.dumps(value, ensure_ascii=False)


def _exists(p):
    return p if p and Path(p).exists() else None


def get_brand(ctx) -> dict:
    with ctx.db.session() as s:
        ident = _get(s, "identity", {})
        colors = {**DEFAULT_BRAND["colors"], **_get(s, "colors", {})}
        files = {k: _exists(_get(s, k)) for k in FILE_SLOTS}
        export = {**DEFAULT_EXPORT, **_get(s, "export", {})}
    return {"name": ident.get("name") or DEFAULT_BRAND["name"],
            "tagline": ident.get("tagline") or DEFAULT_BRAND["tagline"],
            "colors": colors, "files": files, "export": export,
            "fonts": {"heading": files["font_heading"], "body": files["font_body"]},
            "logo": files["logo"], "watermark": files["watermark"], "intro": files["intro"],
            "outro": files["outro"]}


def public_brand(b: dict) -> dict:
    return {"name": b["name"], "tagline": b["tagline"], "colors": b["colors"], "export": b["export"],
            "files": {k: bool(v) for k, v in b["files"].items()}}


def update_brand(ctx, name=None, tagline=None, colors=None, export=None) -> dict:
    with ctx.db.session() as s:
        ident = _get(s, "identity", {})
        if name is not None:
            ident["name"] = name.strip()[:60]
        if tagline is not None:
            ident["tagline"] = tagline.strip()[:120]
        _put(s, "identity", ident)
        if colors:
            cur = _get(s, "colors", {})
            for k, v in colors.items():
                if k not in DEFAULT_BRAND["colors"]:
                    raise BrandError(f"Unknown colour '{k}'")
                if not (isinstance(v, str) and len(v) == 7 and v.startswith("#")):
                    raise BrandError(f"Colour '{k}' must look like #RRGGBB")
                int(v[1:], 16)
                cur[k] = v.upper()
            _put(s, "colors", cur)
        if export:
            cur = _get(s, "export", {})
            for k, v in export.items():
                if k not in DEFAULT_EXPORT or len(v) != 2 or not all(isinstance(x, int) and 200 <= x <= 4320 for x in v):
                    raise BrandError(f"Invalid export size for '{k}'")
                cur[k] = list(v)
            _put(s, "export", cur)
    return public_brand(get_brand(ctx))


def save_brand_file(ctx, slot: str, stream, filename: str, max_bytes: int = 500 * 1024 * 1024) -> dict:
    if slot not in FILE_SLOTS:
        raise BrandError(f"Unknown brand file slot '{slot}'")
    name = sanitize_filename(filename, slot)
    ext = Path(name).suffix.lower()
    if ext not in FILE_SLOTS[slot]:
        raise BrandError(f"{slot} must be one of: {', '.join(sorted(FILE_SLOTS[slot]))}")
    dest = ctx.settings.brand_dir / f"{slot}_{uuid.uuid4().hex[:8]}{ext}"
    total = 0
    with open(dest, "wb") as f:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise BrandError("File is too large")
            f.write(chunk)
    if ext in IMAGE_EXTENSIONS:
        from PIL import Image
        try:
            Image.open(dest).verify()
        except Exception:
            dest.unlink(missing_ok=True)
            raise BrandError("Not a valid image")
    elif ext in VIDEO_EXTENSIONS:
        from ruha.services import media
        try:
            media.probe(dest)
        except media.MediaError as e:
            dest.unlink(missing_ok=True)
            raise BrandError(f"Not a valid video: {e}")
    with ctx.db.session() as s:
        old = _get(s, slot)
        _put(s, slot, str(dest))
    if old and Path(old).exists() and Path(old) != dest:
        Path(old).unlink(missing_ok=True)
    return public_brand(get_brand(ctx))


def clear_brand_file(ctx, slot: str) -> dict:
    if slot not in FILE_SLOTS:
        raise BrandError(f"Unknown brand file slot '{slot}'")
    with ctx.db.session() as s:
        old = _get(s, slot)
        _put(s, slot, None)
    if old:
        Path(old).unlink(missing_ok=True)
    return public_brand(get_brand(ctx))


# ------------------------------- templates ---------------------------------
DEFAULT_TEMPLATES = [
    ("poster", "RUHA Classic Poster", {"layout": "classic"}, True),
    ("social", "RUHA Classic Post", {"layout": "classic"}, True),
    ("social", "RUHA Band Post", {"layout": "band"}, False),
    ("social", "RUHA Centered Post", {"layout": "centered"}, False),
    ("reel", "RUHA Vertical Reel", {"width": 1080, "height": 1920, "subtitles": True}, True),
]


def ensure_default_templates(ctx):
    with ctx.db.session() as s:
        if s.query(Template).count():
            return
        for kind, name, cfg, default in DEFAULT_TEMPLATES:
            s.add(Template(kind=kind, name=name, config_json=json.dumps(cfg), is_default=default))


def template_dict(t: Template) -> dict:
    return {"id": t.id, "kind": t.kind, "name": t.name, "config": t.config, "is_default": t.is_default}


def layout_for(s, kind: str, index: int = 0, template_id: int | None = None) -> tuple:
    """Pick a template layout. For social posts successive indices rotate through distinct layouts."""
    if template_id:
        t = s.get(Template, template_id)
        if t:
            return t.config.get("layout", "classic"), t.id
    rows = s.query(Template).filter_by(kind=kind).order_by(Template.id).all()
    if not rows:
        return "classic", None
    if kind == "social":
        t = rows[index % len(rows)]
    else:
        t = next((r for r in rows if r.is_default), rows[0])
    return t.config.get("layout", "classic"), t.id
