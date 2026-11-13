import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "content_agent.db"

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            asset_path TEXT NOT NULL,
            decision TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );
        ''')

def create_project(project_id, title, source_path, output_dir):
    with connect() as c:
        c.execute(
            "INSERT INTO projects(id,title,source_path,status,created_at,output_dir) VALUES(?,?,?,?,?,?)",
            (project_id, title, source_path, "queued",
             datetime.now(timezone.utc).isoformat(), output_dir)
        )

def update_status(project_id, status, error=None):
    with connect() as c:
        c.execute("UPDATE projects SET status=?, error=? WHERE id=?",
                  (status, error, project_id))

def add_asset(project_id, asset_type, path, metadata=None):
    import json
    with connect() as c:
        c.execute(
            "INSERT INTO assets(project_id,asset_type,path,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (project_id, asset_type, str(path), json.dumps(metadata or {}),
             datetime.now(timezone.utc).isoformat())
        )

def list_projects(limit=30):
    with connect() as c:
        return c.execute("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

def add_feedback(project_id, asset_path, decision, note=""):
    with connect() as c:
        c.execute(
            "INSERT INTO feedback(project_id,asset_path,decision,note,created_at) VALUES(?,?,?,?,?)",
            (project_id, asset_path, decision, note,
             datetime.now(timezone.utc).isoformat())
        )
