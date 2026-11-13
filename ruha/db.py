"""Database engine, sessions and the SQL-file migration runner.

Migrations live in ``ruha/migrations/NNNN_name.sql`` and are applied once, in order,
inside a transaction; applied versions are recorded in ``schema_migrations``.
``0001_initial.sql`` is generated from the models by ``python -m ruha.make_initial_migration``
and then frozen - later schema changes must be new numbered files.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.engine = self._make_engine()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def _make_engine(self):
        engine = create_engine(f"sqlite:///{self.path}", future=True,
                               connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine

    @contextmanager
    def session(self):
        """Transactional scope: commit on success, roll back on error."""
        s: Session = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self):
        self.engine.dispose()

    def reopen(self):
        """Used after a restore replaced the database file."""
        self.engine.dispose()
        self.engine = self._make_engine()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    # -------- migrations --------------------------------------------------
    def migrate(self) -> list:
        applied_now = []
        files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        conn = sqlite3.connect(self.path, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                         "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for f in files:
                version = int(f.name[:4])
                if version in done:
                    continue
                sql = f.read_text(encoding="utf-8")
                try:
                    conn.execute("BEGIN")
                    for stmt in _split_sql(sql):
                        conn.execute(stmt)
                    conn.execute("INSERT INTO schema_migrations VALUES (?,?,datetime('now'))",
                                 (version, f.stem))
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                applied_now.append(f.name)
        finally:
            conn.close()
        return applied_now

    def schema_version(self) -> int:
        with self.engine.connect() as c:
            try:
                return c.execute(text("SELECT COALESCE(MAX(version),0) FROM schema_migrations")).scalar() or 0
            except Exception:
                return 0


def _split_sql(sql: str) -> list:
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]
