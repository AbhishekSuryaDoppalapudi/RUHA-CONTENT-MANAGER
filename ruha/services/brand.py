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


def get_brand(ctx, project_id: int | None = None) -> dict:
    """The organisation brand. With ``project_id`` the project's own logo (if uploaded) replaces the logo and
    the reel watermark, and the project's text templates are included under ``texts``."""
    with ctx.db.session() as s:
        ident = _get(s, "identity", {})
        colors = {**DEFAULT_BRAND["colors"], **_get(s, "colors", {})}
        files = {k: _exists(_get(s, k)) for k in FILE_SLOTS}
        export = {**DEFAULT_EXPORT, **_get(s, "export", {})}
        kit = (_get(s, _kit_key(project_id), {}) or {}) if project_id else {}
    plogo = _exists(kit.get("logo"))
    if plogo:
        files["logo"] = files["watermark"] = plogo
    return {"name": ident.get("name") or DEFAULT_BRAND["name"],
            "tagline": ident.get("tagline") or DEFAULT_BRAND["tagline"],
            "colors": colors, "files": files, "export": export,
            "fonts": {"heading": files["font_heading"], "body": files["font_body"]},
            "logo": files["logo"], "watermark": files["watermark"], "intro": files["intro"],
            "outro": files["outro"], "texts": list(kit.get("texts", [])), "project_logo": bool(plogo)}


# --------------------- per-project brand kit (logo + text templates) ---------------------
TEXT_KINDS = {"closing": "Reel closing line", "caption": "Caption footer / call to action", "hashtags": "Hashtags"}
MAX_TEXT = 600


def _kit_key(pid: int) -> str:
    return f"project:{int(pid)}"


def get_kit(ctx, pid: int) -> dict:
    with ctx.db.session() as s:
        kit = _get(s, _kit_key(pid), {}) or {}
    return {"logo": _exists(kit.get("logo")), "texts": list(kit.get("texts", []))}


def public_kit(kit: dict) -> dict:
    return {"has_logo": bool(kit["logo"]), "texts": kit["texts"], "kinds": TEXT_KINDS}


def _save_kit(ctx, pid: int, kit: dict):
    with ctx.db.session() as s:
        _put(s, _kit_key(pid), {"logo": kit.get("logo"), "texts": kit.get("texts", [])})


def save_project_logo(ctx, pid: int, ws, stream, filename: str, max_bytes: int = 20 * 1024 * 1024) -> dict:
    name = sanitize_filename(filename, "logo")
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise BrandError(f"The logo must be one of: {', '.join(sorted(IMAGE_EXTENSIONS))}")
    d = Path(ws) / "brand"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"logo_{uuid.uuid4().hex[:8]}{ext}"
    total = 0
    with open(dest, "wb") as f:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise BrandError("The logo file is too large (20 MB maximum)")
            f.write(chunk)
    from PIL import Image
    try:
        Image.open(dest).verify()
    except Exception:
        dest.unlink(missing_ok=True)
        raise BrandError("That file is not a valid image")
    kit = get_kit(ctx, pid)
    old = kit["logo"]
    kit["logo"] = str(dest)
    _save_kit(ctx, pid, kit)
    if old and Path(old) != dest:
        Path(old).unlink(missing_ok=True)
    return public_kit(get_kit(ctx, pid))


def clear_project_logo(ctx, pid: int) -> dict:
    kit = get_kit(ctx, pid)
    old, kit["logo"] = kit["logo"], None
    _save_kit(ctx, pid, kit)
    if old:
        Path(old).unlink(missing_ok=True)
    return public_kit(get_kit(ctx, pid))


def _clean_text(kind: str, name: str, text: str) -> dict:
    if kind not in TEXT_KINDS:
        raise BrandError(f"kind must be one of {sorted(TEXT_KINDS)}")
    text = " ".join(str(text or "").split()) if kind != "caption" else "\n".join(
        " ".join(ln.split()) for ln in str(text or "").strip().splitlines()).strip()
    if not text:
        raise BrandError("The text is empty")
    if len(text) > MAX_TEXT:
        raise BrandError(f"Keep each text under {MAX_TEXT} characters")
    return {"kind": kind, "name": (name or text)[:60].strip(), "text": text}


def add_text(ctx, pid: int, kind: str, name: str, text: str) -> dict:
    kit = get_kit(ctx, pid)
    if len(kit["texts"]) >= 200:
        raise BrandError("A project can hold at most 200 text templates")
    kit["texts"].append({"id": uuid.uuid4().hex[:8], **_clean_text(kind, name, text)})
    _save_kit(ctx, pid, kit)
    return public_kit(kit)


def update_text(ctx, pid: int, tid: str, kind: str, name: str, text: str) -> dict:
    kit = get_kit(ctx, pid)
    for i, t in enumerate(kit["texts"]):
        if t["id"] == tid:
            kit["texts"][i] = {"id": tid, **_clean_text(kind, name, text)}
            _save_kit(ctx, pid, kit)
            return public_kit(kit)
    raise BrandError("Text template not found")


def delete_text(ctx, pid: int, tid: str) -> dict:
    kit = get_kit(ctx, pid)
    kit["texts"] = [t for t in kit["texts"] if t["id"] != tid]
    _save_kit(ctx, pid, kit)
    return public_kit(kit)


def import_texts(ctx, pid: int, kind: str, stream, filename: str) -> dict:
    """Upload a .txt (one template per line) or .json ([{"kind","name","text"}, ...] or ["text", ...]) file."""
    ext = Path(filename or "").suffix.lower()
    if ext not in (".txt", ".json", ".md"):
        raise BrandError("Upload a .txt file (one template per line) or a .json file")
    raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise BrandError("The file is too large (1 MB maximum)")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BrandError("The file must be UTF-8 text")
    items = []
    if ext == ".json":
        try:
            data = json.loads(content)
        except ValueError:
            raise BrandError("That is not valid JSON")
        for it in (data if isinstance(data, list) else data.get("texts", []) if isinstance(data, dict) else []):
            if isinstance(it, str):
                items.append((kind, "", it))
            elif isinstance(it, dict):
                items.append((it.get("kind", kind), it.get("name", ""), it.get("text", "")))
    else:
        items = [(kind, "", ln.strip()) for ln in content.splitlines() if ln.strip() and not ln.startswith("#!")]
    if not items:
        raise BrandError("No text templates found in the file")
    kit = get_kit(ctx, pid)
    for k, n, t in items:
        if len(kit["texts"]) >= 200:
            break
        kit["texts"].append({"id": uuid.uuid4().hex[:8], **_clean_text(k, n, t)})
    _save_kit(ctx, pid, kit)
    return public_kit(kit)


def forget_project_kit(ctx, pid: int):
    with ctx.db.session() as s:
        row = s.get(BrandSetting, _kit_key(pid))
        if row is not None:
            s.delete(row)


def closing_text(brand: dict, default: str) -> str:
    """The project's own closing line when one was uploaded, otherwise ``default``."""
    for t in brand.get("texts", []):
        if t["kind"] == "closing":
            return t["text"]
    return default


def with_texts(brand: dict, caption: str, hashtags: list | None = None) -> tuple:
    """Append the project's caption footer and merge its hashtags (never duplicates)."""
    caption = (caption or "").strip()
    for t in brand.get("texts", []):
        if t["kind"] == "caption" and t["text"] not in caption:
            caption = f"{caption}\n\n{t['text']}" if caption else t["text"]
            break
    tags = list(hashtags or [])
    seen = {x.lower() for x in tags}
    for t in brand.get("texts", []):
        if t["kind"] == "hashtags":
            for w in t["text"].replace(",", " ").split():
                w = w if w.startswith("#") else f"#{w}"
                if w.lower() not in seen:
                    tags.append(w)
                    seen.add(w.lower())
    return caption, tags


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
    ("poster", "El Ruach Classic Poster", {"layout": "classic"}, True),
    ("social", "El Ruach Classic Post", {"layout": "classic"}, True),
    ("social", "El Ruach Band Post", {"layout": "band"}, False),
    ("social", "El Ruach Centered Post", {"layout": "centered"}, False),
    ("reel", "El Ruach Vertical Reel", {"width": 1080, "height": 1920, "subtitles": True}, True),
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
