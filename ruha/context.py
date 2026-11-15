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
        if self.settings.start_worker:
            self.queue.start()

    def stop(self):
        self.queue.stop()
        self.db.dispose()
