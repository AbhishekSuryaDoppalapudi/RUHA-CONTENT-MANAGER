"""Unit tests: security helpers, database/migrations, planner guards, subtitles, memory."""
import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from ruha.db import Database
from ruha.models import GeneratedAsset, Project, SourceVideo
from ruha.security import (PathSecurityError, ensure_within, redact, safe_extract_names, safe_join,
                           sanitize_filename, slugify)
from ruha.services import planner
from ruha.services.subtitles import build_cues, build_srt, srt_time
from conftest import make_segments


# ------------------------------- security ------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"), ("C:\\Windows\\system32\\cmd.exe", "cmd.exe"), ("my video (1).MP4", "my_video__1.mp4"),
    ("CON.mp4", "_CON.mp4"), ("", "file"), ("..", "file"), ("a" * 400 + ".mp4", None)])
def test_sanitize_filename(raw, expected):
    out = sanitize_filename(raw)
    if expected:
        assert out == expected
    else:
        assert len(out) <= 120 and out.endswith(".mp4")
    assert "/" not in out and "\\" not in out and ".." not in out


def test_safe_join_blocks_traversal(tmp_path):
    assert safe_join(tmp_path, "a", "b.txt").parent.name == "a"
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "..", "x")
    with pytest.raises(PathSecurityError):
        ensure_within(tmp_path.parent / "other.txt", [tmp_path])


def test_zip_slip_names_rejected():
    safe_extract_names(["manifest.json", "projects/a/b.mp4"])
    for bad in ["../evil.txt", "/abs/x", "projects/../../evil", "C:/x.txt"]:
        with pytest.raises(PathSecurityError):
            safe_extract_names([bad])


def test_redact_hides_user_dir_and_tokens():
    out = redact(r"failed at C:\Users\Jane Doe\file hf_abcdefghijklmnop token")
    assert "Jane" not in out and "hf_abc" not in out


def test_slugify():
    assert slugify("Sunday Grace! 2026") == "sunday-grace-2026"
    assert slugify("தமிழ்") == "project"


# -------------------------------- database -----------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.migrate()
    return d


def test_migrations_create_all_required_tables_and_are_idempotent(db):
    required = {"projects", "source_videos", "transcripts", "transcript_segments", "translations", "generated_assets",
                "reels", "posters", "social_posts", "subtitles", "processing_jobs", "job_logs", "approvals",
                "feedback", "content_tags", "templates", "brand_settings", "content_calendar", "model_settings",
                "memory_references", "audit_logs"}
    con = sqlite3.connect(db.path)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert required <= tables
    assert db.migrate() == []          # nothing re-applied
    assert db.schema_version() >= 1


def test_foreign_keys_enforced_and_cascade(db):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            s.add(GeneratedAsset(project_id=999, kind="poster"))
    with db.session() as s:
        p = Project(name="x", workspace_dir="w")
        s.add(p)
        s.flush()
        s.add(SourceVideo(project_id=p.id, original_name="a.mp4", path="p", sha256="h"))
        s.add(GeneratedAsset(project_id=p.id, kind="poster"))
        pid = p.id
    with db.session() as s:
        s.delete(s.get(Project, pid))
    with db.session() as s:
        assert s.query(SourceVideo).count() == 0 and s.query(GeneratedAsset).count() == 0


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db.session() as s:
            s.add(Project(name="temp", workspace_dir="w"))
            s.flush()
            raise RuntimeError("boom")
    with db.session() as s:
        assert s.query(Project).count() == 0


def test_indexes_exist(db):
    con = sqlite3.connect(db.path)
    idx = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
    assert "ix_assets_project_kind" in idx and "ix_projects_status" in idx


# ------------------------------- subtitles -----------------------------------
def test_srt_time_and_cues(tmp_path):
    assert srt_time(3661.5) == "01:01:01,500"
    segs = [{"start": 0, "end": 10, "text": "a " * 40}, {"start": 10, "end": 20, "text": "tail"}]
    cues = build_cues(segs, 5, 15, max_chars=20)
    assert cues and cues[0][0] == 0 and max(c[1] for c in cues) <= 10.0001
    out = build_srt(segs, 5, 15, tmp_path / "x.srt")
    assert out.read_text(encoding="utf-8").startswith("1\n00:00:00,000 -->")


# --------------------------------- planner -----------------------------------
FULL = " ".join(s["text"] for s in make_segments())


def _plan(**over):
    p = {"title": "T", "main_theme": "m", "summary": "s", "themes": ["grace"], "quotes": [],
         "main_reels": [{"start": 1, "end": 46}, {"start": 52, "end": 100}, {"start": 106, "end": 158}],
         "short_reels": [{"start": 162, "end": 182}, {"start": 183, "end": 200}],
         "poster": {"headline": "H", "quote": "never said this"},
         "encouragement_posts": [{"headline": "a", "body": "b", "reference": "Romans 8"},
                                 {"headline": "c", "body": "d", "reference": "Segment"}]}
    p.update(over)
    return p


def test_normalize_snaps_and_bounds():
    out = planner.normalize_plan(_plan(), make_segments(), 200, FULL)
    assert out["main_reels"][0]["start"] == 0 and out["main_reels"][0]["end"] == 50
    for r in out["main_reels"]:
        assert planner.MAIN_RANGE[0] <= r["end"] - r["start"] <= planner.MAIN_RANGE[1]
    for r in out["short_reels"]:
        assert planner.SHORT_RANGE[0] <= r["end"] - r["start"] <= planner.SHORT_RANGE[1]


def test_unspoken_quotes_and_references_are_removed():
    out = planner.normalize_plan(_plan(), make_segments(), 200, FULL)
    assert out["poster"]["quote"] == ""                           # invented quote dropped
    assert out["encouragement_posts"][0]["reference"] == ""       # "Romans" never spoken
    assert out["encouragement_posts"][1]["reference"] == "Segment"  # spoken word kept


def test_wrong_counts_and_overlap_rejected():
    with pytest.raises(planner.PlanError):
        planner.normalize_plan(_plan(main_reels=[{"start": 0, "end": 50}]), make_segments(), 200, FULL)
    overlap = [{"start": 0, "end": 50}, {"start": 5, "end": 55}, {"start": 106, "end": 158}]
    with pytest.raises(planner.PlanError):
        planner.normalize_plan(_plan(main_reels=overlap), make_segments(), 200, FULL)


def test_create_plan_retries_then_succeeds():
    class Flaky:
        n = 0

        def generate_json(self, prompt, cfg):
            Flaky.n += 1
            return _plan(main_reels=[]) if Flaky.n == 1 else _plan()
    plan = planner.create_plan(Flaky(), {}, make_segments(), 200, {}, log=lambda *a, **k: None)
    assert Flaky.n == 2 and len(plan["main_reels"]) == 3


def test_long_transcript_uses_chunked_analysis():
    segs = [{"start": float(i * 5), "end": float(i * 5 + 5), "text": "word " * 60 + f"seg{i}"} for i in range(400)]
    calls = []

    class LLM:
        def generate_json(self, prompt, cfg):
            calls.append("PART" if "PART of a longer" in prompt else "REDUCE")
            return {"summary": "x", "themes": [], "quotes": [], "moments": []} if calls[-1] == "PART" else _plan(
                main_reels=[{"start": 0, "end": 60}, {"start": 200, "end": 260}, {"start": 500, "end": 560}],
                short_reels=[{"start": 900, "end": 920}, {"start": 1200, "end": 1220}])
    plan = planner.create_plan(LLM(), {}, segs, 2000, {}, log=lambda *a, **k: None)
    assert calls.count("PART") >= 2 and calls[-1] == "REDUCE" and len(plan["short_reels"]) == 2


# ---------------------------------- memory -----------------------------------
def test_memory_add_search_similar_delete_export(client, ctx):
    m = ctx.memory
    pid = client.post("/api/projects", json={"name": "Mem"}).json()["id"]
    m.add("caption", "Grace is a free gift from God to every person", pid, None, {"outcome": "approved"})
    m.add("caption", "Quarterly tax filing deadlines and invoices", pid)
    hits = m.search("free gift of grace", n=2)
    assert hits[0]["text"].startswith("Grace is a free gift")
    assert m.similar("Grace is a free gift from God to every person", ["caption"], 0.95)
    assert not m.similar("completely unrelated astronomy telescope", ["caption"], 0.8)
    assert m.search("grace", outcome="approved")[0]["meta"]["outcome"] == "approved"
    assert m.stats()["total"] == 2 and m.export()["memory"]
    assert m.delete(kinds=["caption"]) == 2 and m.stats()["total"] == 0 and m.search("grace") == []


def test_memory_reindex_rebuilds_vectors(client, ctx):
    ctx.memory.add("preference", "Always keep captions short and warm", None)
    ctx.memory.delete  # noqa: B018
    for col in ctx.memory._all_collections():
        ctx.memory.client().delete_collection(col.name)
    assert ctx.memory.search("short warm captions") != [] or ctx.memory.reindex() >= 0
    assert ctx.memory.search("short warm captions")[0]["kind"] == "preference"
