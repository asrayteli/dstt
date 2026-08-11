from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select

from app.models import db
from scripts.migrate_sqlite_to_postgres import migrate


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("DSTT_TEST_POSTGRES_URI"), reason="PostgreSQL test URI is not configured")
def test_empty_schema_and_user_row_migrate_to_postgresql(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "source.db"
    sqlite_engine = create_engine(f"sqlite+pysqlite:///{sqlite_path.as_posix()}")
    db.metadata.create_all(sqlite_engine)
    users = db.metadata.tables["users"]
    with sqlite_engine.begin() as connection:
        connection.execute(
            users.insert(),
            {
                "id": 7,
                "username": "migration-user",
                "password_hash": "not-a-real-password-hash",
                "name": "Migration Test",
                "is_admin": False,
            },
        )
    sqlite_engine.dispose()

    target_uri = os.environ["DSTT_TEST_POSTGRES_URI"]
    result = migrate(sqlite_path, target_uri, Path(__file__).resolve().parents[1])

    assert result["row_count_verification"] == "ok"
    assert result["migrated_tables"] == 66
    target_engine = create_engine(target_uri)
    with target_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(users)) == 1
        assert connection.scalar(select(users.c.username)) == "migration-user"
    target_engine.dispose()
