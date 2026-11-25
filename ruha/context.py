"""Application context: settings, database, runtime config and replaceable AI services."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ruha.ai.embeddings import make_embedder
from ruha.ai.llm import OllamaLLM
from ruha.ai.whisper import FasterWhisperTranscriber
from ruha.config import DEFAULT_CONFIG, Settings
from ruha.db import Database
from ruha.models import AuditLog, ModelSetting

log = logging.getLogger("ruha")


class ConfigStore:
    """Runtime configuration persisted in ``model_settings`` with defaults from config.py."""

    def __init__(self, db: Database):
        self.db = db

    def all(self) -> dict:
        values = {k: v["value"] for k, v in DEFAULT_CONFIG.items()}
        with self.db.session() as s:
            for row in s.query(ModelSetting).all():
                try:
                    values[row.key] = json.loads(row.value_json)
                except ValueError:
                    pass
        return values

    def get(self, key: str, default=None):
        return self.all().get(key, default)

    def set_many(self, updates: dict) -> dict:
        unknown = [k for k in updates if k not in DEFAULT_CONFIG]
        if unknown:
            raise KeyError(f"Unknown setting(s): {', '.join(unknown)}")
        with self.db.session() as s:
            for k, v in updates.items():
                row = s.get(ModelSetting, k)
                if row is None:
                    s.add(ModelSetting(key=k, category=DEFAULT_CONFIG[k]["category"],
                                       value_json=json.dumps(v)))
                else:
                    row.value_json = json.dumps(v)
        return self.all()

    OLD_DEFAULTS = {"whisper.model": ("small", "large-v3-turbo"), "llm.model": ("qwen2.5:3b", "qwen2.5:7b")}

    def migrate_model_defaults(self) -> list:
        """Once per database: a stored old default (whisper 'small', qwen2.5:3b) becomes the new default
        (large-v3-turbo / qwen2.5:7b). Anything you chose yourself is left alone, and it runs only once."""
        changed = []
        with self.db.session() as s:
            if s.get(ModelSetting, "app.model_defaults_v2") is not None:
                return changed
            for key, (old, new) in self.OLD_DEFAULTS.items():
                row = s.get(ModelSetting, key)
                if row is not None and json.loads(row.value_json) == old:
                    row.value_json = json.dumps(new)
                    changed.append(key)
            s.add(ModelSetting(key="app.model_defaults_v2", category="app", value_json=json.dumps(True)))
        return changed

    def by_category(self) -> dict:
        vals = self.all()
        out: dict = {}
        for k, meta in DEFAULT_CONFIG.items():
            out.setdefault(meta["category"], {})[k] = vals[k]
        return out


@dataclass
class Services:
    """Swappable AI back-ends (tests replace these with fakes)."""
    transcriber: object = field(default_factory=FasterWhisperTranscriber)
    llm: object = field(default_factory=OllamaLLM)
    embedder_factory: object = make_embedder
    translator: object = None  # set in AppContext (needs settings)
    images: object = field(default_factory=lambda: __import__("ruha.creative.backends", fromlist=["x"]).ImageService())
    imagegen: object = field(default_factory=lambda: __import__("ruha.ai.imagegen", fromlist=["x"]).DiffusersBackground())


class AppContext:
    def __init__(self, settings: Settings, services: Services | None = None):
        from ruha.ai.translator import TranslatorBridge
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.db.migrate()
        self.config = ConfigStore(self.db)
        self.services = services or Services()
        if self.services.translator is None:
            self.services.translator = TranslatorBridge(settings)
        self.model_lock = threading.RLock()   # one heavy model at a time
        from ruha.services.memory import MemoryStore
        self.memory = MemoryStore(self)
        from ruha.jobs import JobQueue
        self.queue = JobQueue(self)
        self.maintenance = threading.Event()  # set during backup-restore

    # convenience ---------------------------------------------------------
    def audit(self, session, entity_type: str, entity_id, action: str, **detail):
        session.add(AuditLog(entity_type=entity_type, entity_id=entity_id, action=action,
                             actor="owner",
                             detail_json=json.dumps(detail, ensure_ascii=False, default=str) if detail else None))

    def start(self):
        self.queue.recover_interrupted()
        try:
            moved = self.config.migrate_model_defaults()
            if moved:
                logging.getLogger("ruha").info("Model defaults updated once: %s", ", ".join(moved))
        except Exception:
            logging.getLogger("ruha").exception("model default migration failed")
        if self.settings.start_worker:
            try:                                          # empty leftover project folders (no files) are safe to remove
                from ruha.services import projects as project_svc
                with self.db.session() as s:
                    res = project_svc.cleanup_orphan_workspaces(self, s, dry_run=False, include_files=False)
                if res["removed"]:
                    logging.getLogger("ruha").info("Removed %d empty orphan project folder(s): %s", len(res["removed"]),
                                                   ", ".join(o["name"] for o in res["removed"]))
            except Exception:
                logging.getLogger("ruha").exception("orphan folder cleanup failed")
            self.queue.start()
            from ruha.services import system as sysinfo
            sysinfo.probe_cache(self).warm()            # first status call already has the slow probes

    def stop(self):
        self.queue.stop()
        self.db.dispose()
