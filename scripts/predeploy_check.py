#!/usr/bin/env python3
"""Fast checks that are safe to run inside the production virtualenv."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import db


def main() -> int:
    tables = list(db.metadata.sorted_tables)
    if len(tables) != 66:
        raise RuntimeError(f"Expected 66 model tables, found {len(tables)}")
    dialect = postgresql.dialect()
    for table in tables:
        statement = str(CreateTable(table).compile(dialect=dialect))
        if "AUTOINCREMENT" in statement:
            raise RuntimeError(f"SQLite-only DDL remains in table {table.name}")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    if ScriptDirectory.from_config(config).get_heads() != ["20260811_0001"]:
        raise RuntimeError("Unexpected Alembic head")
    print("predeploy=ok tables=66 postgres_ddl=ok alembic=20260811_0001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
