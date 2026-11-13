"""SQLAlchemy models. Foreign keys are enforced (PRAGMA foreign_keys=ON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class JSONMixin:
    @staticmethod
    def _dump(v) -> str | None:
        return None if v is None else json.dumps(v, ensure_ascii=False)

    @staticmethod
    def _load(s, default=None):
        if not s:
            return default
        try:
            return json.loads(s)
        except ValueError:
            return default


class Project(Base, JSONMixin):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    content_type: Mapped[str] = mapped_column(String(40), default="sermon", index=True)
    workspace_dir: Mapped[str] = mapped_column(String(500))
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status_before_archive: Mapped[str | None] = mapped_column(String(30), nullable=True)

    videos = relationship("SourceVideo", back_populates="project", cascade="all, delete-orphan")
    transcripts = relationship("Transcript", back_populates="project", cascade="all, delete-orphan")
    assets = relationship("GeneratedAsset", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("ProcessingJob", back_populates="project", cascade="all, delete-orphan")


class SourceVideo(Base):
    __tablename__ = "source_videos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0)
    video_codec: Mapped[str | None] = mapped_column(String(40), nullable=True)
    audio_tracks: Mapped[int] = mapped_column(Integer, default=0)
    audio_codec: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project = relationship("Project", back_populates="videos")


class Transcript(Base, JSONMixin):
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("source_videos.id", ondelete="SET NULL"), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    full_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    themes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quotes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # content plan from the LLM
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project = relationship("Project", back_populates="transcripts")
    segments = relationship("TranscriptSegment", back_populates="transcript",
                            cascade="all, delete-orphan", order_by="TranscriptSegment.idx")

    @property
    def themes(self): return self._load(self.themes_json, [])
    @property
    def quotes(self): return self._load(self.quotes_json, [])
    @property
    def plan(self): return self._load(self.plan_json, None)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    transcript = relationship("Transcript", back_populates="segments")


class Translation(Base):
    __tablename__ = "translations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("source_videos.id", ondelete="SET NULL"), nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_language: Mapped[str] = mapped_column(String(40))
    dubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    voice: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    srt_asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="SET NULL"), nullable=True)
    video_asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="SET NULL"), nullable=True)
    audio_asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GeneratedAsset(Base, JSONMixin):
    """Every reviewable/downloadable item produced for a project."""
    __tablename__ = "generated_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("source_videos.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    slot: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. main_1, short_2, poster
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    preview_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    project = relationship("Project", back_populates="assets")
    versions = relationship("AssetVersion", back_populates="asset", cascade="all, delete-orphan",
                            order_by="AssetVersion.version")
    __table_args__ = (Index("ix_assets_project_kind", "project_id", "kind"),)

    @property
    def hashtags(self): return self._load(self.hashtags_json, [])
    @property
    def meta(self): return self._load(self.metadata_json, {})


class AssetVersion(Base):
    """Immutable snapshot of an asset taken whenever it is edited or regenerated."""
    __tablename__ = "asset_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(80), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    asset = relationship("GeneratedAsset", back_populates="versions")
    __table_args__ = (UniqueConstraint("asset_id", "version"),)


class Reel(Base):
    __tablename__ = "reels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), unique=True)
    category: Mapped[str] = mapped_column(String(10))  # main | short
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    hook: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    transcript_excerpt: Mapped[str] = mapped_column(Text, default="")
    subtitle_asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="SET NULL"), nullable=True)


class Poster(Base):
    __tablename__ = "posters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), unique=True)
    headline: Mapped[str] = mapped_column(Text, default="")
    subheadline: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str] = mapped_column(Text, default="")
    theme: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)


class SocialPost(Base):
    __tablename__ = "social_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), unique=True)
    headline: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)


class Subtitle(Base):
    __tablename__ = "subtitles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(40), default="original")
    format: Mapped[str] = mapped_column(String(10), default="srt")
    path: Mapped[str] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProcessingJob(Base, JSONMixin):
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    step: Mapped[str] = mapped_column(String(200), default="")
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    depends_on_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    project = relationship("Project", back_populates="jobs")
    logs = relationship("JobLog", back_populates="job", cascade="all, delete-orphan",
                        order_by="JobLog.id")

    @property
    def params(self): return self._load(self.params_json, {})
    @property
    def result(self): return self._load(self.result_json, {})


class JobLog(Base):
    __tablename__ = "job_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    job = relationship("ProcessingJob", back_populates="logs")


class Approval(Base):
    """Review events for an asset: approve / reject / revise / comment."""
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), index=True)
    asset_version: Mapped[int] = mapped_column(Integer, default=1)
    action: Mapped[str] = mapped_column(String(20))   # approved|rejected|revision_requested|comment
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    reason: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Feedback(Base):
    """Learning signal for the memory module (derived from approvals + explicit notes)."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))      # positive | negative | revision | note
    note: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[str] = mapped_column(Text, default="")   # the text that was judged
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentTag(Base):
    __tablename__ = "content_tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))   # project | asset
    entity_id: Mapped[int] = mapped_column(Integer)
    tag: Mapped[str] = mapped_column(String(60), index=True)
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "tag"),
                      Index("ix_tags_entity", "entity_type", "entity_id"))


class Template(Base, JSONMixin):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)   # poster|reel|social|subtitle|intro|outro|watermark
    name: Mapped[str] = mapped_column(String(100))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    file_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def config(self): return self._load(self.config_json, {})


class BrandSetting(Base):
    __tablename__ = "brand_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CalendarEntry(Base):
    __tablename__ = "content_calendar"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    campaign: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(60), default="")
    platform: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelSetting(Base):
    __tablename__ = "model_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    category: Mapped[str] = mapped_column(String(30), default="ai", index=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MemoryReference(Base, JSONMixin):
    """One row per vector stored in ChromaDB, so memory can be inspected/exported/deleted."""
    __tablename__ = "memory_references"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vector_id: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("generated_assets.id", ondelete="CASCADE"), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def meta(self): return self._load(self.metadata_json, {})


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
