"""Generate migrations/0001_initial.sql from the current models (run once, then freeze)."""
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from ruha.db import MIGRATIONS_DIR
from ruha.models import Base


def build() -> str:
    out = ["-- RUHA Manager initial schema (generated; do not edit - add a new migration instead)"]
    dialect = sqlite.dialect()
    for table in Base.metadata.sorted_tables:
        out.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            out.append(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(out) + "\n"


if __name__ == "__main__":
    target = MIGRATIONS_DIR / "0001_initial.sql"
    if target.exists():
        raise SystemExit(f"{target.name} already exists - refusing to overwrite a frozen migration")
    target.write_text(build(), encoding="utf-8")
    print("wrote", target)
