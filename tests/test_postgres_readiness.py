from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app import _portable_schema_sql, _resolve_database_uri
from app.models import db


def test_all_model_tables_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()
    compiled = [str(CreateTable(table).compile(dialect=dialect)) for table in db.metadata.sorted_tables]
    assert len(compiled) == 66
    assert all("AUTOINCREMENT" not in statement for statement in compiled)


def test_legacy_startup_ddl_is_translated_for_postgresql() -> None:
    source = (
        "CREATE TABLE example (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "enabled BOOLEAN NOT NULL DEFAULT 0, visible BOOLEAN NOT NULL DEFAULT 1, "
        "created_at DATETIME)"
    )
    translated = _portable_schema_sql(source, "postgresql")
    assert "BIGSERIAL PRIMARY KEY" in translated
    assert "DEFAULT FALSE" in translated
    assert "DEFAULT TRUE" in translated
    assert "TIMESTAMP WITHOUT TIME ZONE" in translated


def test_plain_postgresql_uri_selects_psycopg3(monkeypatch) -> None:
    monkeypatch.setenv("DSTT_DATABASE_URI", "postgresql://user:pass@db/dstt")
    assert _resolve_database_uri() == "postgresql+psycopg://user:pass@db/dstt"


def test_alembic_has_one_baseline_head() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20260811_0001"]
