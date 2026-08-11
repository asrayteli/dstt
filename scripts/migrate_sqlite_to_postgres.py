#!/usr/bin/env python3
"""Import a verified DSTT SQLite snapshot into an empty PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, and_, create_engine, exists, func, inspect, or_, select, text
from sqlalchemy.engine import Connection, Engine

from app.models import db


def _sqlite_engine(path: Path) -> Engine:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {path}")
    return create_engine(f"sqlite+pysqlite:///{path.resolve().as_posix()}")


def _postgres_engine(uri: str) -> Engine:
    if uri.startswith("postgres://"):
        uri = "postgresql+psycopg://" + uri[len("postgres://"):]
    elif uri.startswith("postgresql://"):
        uri = "postgresql+psycopg://" + uri[len("postgresql://"):]
    engine = create_engine(uri, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("The target URI must point to PostgreSQL")
    return engine


def _source_metadata(engine: Engine) -> MetaData:
    metadata = MetaData()
    metadata.reflect(engine)
    return metadata


def _foreign_key_validity(table, constraint, source_metadata: MetaData):
    remote_name = next(iter(constraint.elements)).column.table.name
    remote = source_metadata.tables[remote_name]
    local_columns = [table.c[element.parent.name] for element in constraint.elements]
    comparisons = [
        remote.c[element.column.name] == table.c[element.parent.name]
        for element in constraint.elements
    ]
    all_non_null = and_(*(column.is_not(None) for column in local_columns))
    parent_exists = exists(select(1).select_from(remote).where(and_(*comparisons)))
    return or_(~all_non_null, parent_exists)


def _table_filter(table, model_table, source_metadata: MetaData):
    clauses = [
        _foreign_key_validity(table, constraint, source_metadata)
        for constraint in model_table.foreign_key_constraints
        if next(iter(constraint.elements)).column.table.name in source_metadata.tables
    ]
    return and_(*clauses) if clauses else None


def preflight(source_engine: Engine) -> dict[str, object]:
    source_metadata = _source_metadata(source_engine)
    model_metadata = db.metadata
    source_tables = set(source_metadata.tables) - {"alembic_version"}
    model_tables = set(model_metadata.tables)
    missing_models = sorted(source_tables - model_tables)
    missing_source = sorted(model_tables - source_tables)
    orphan_rows: list[dict[str, object]] = []
    length_violations: list[dict[str, object]] = []
    with source_engine.connect() as connection:
        check = str(connection.exec_driver_sql("PRAGMA quick_check").scalar_one())
        for table_name in sorted(source_tables & model_tables):
            source_table = source_metadata.tables[table_name]
            model_table = model_metadata.tables[table_name]
            for column in model_table.columns:
                maximum_length = getattr(column.type, "length", None)
                if not maximum_length or column.name not in source_table.c:
                    continue
                actual_length = int(
                    connection.scalar(select(func.max(func.length(source_table.c[column.name]))))
                    or 0
                )
                if actual_length > maximum_length:
                    length_violations.append(
                        {
                            "table": table_name,
                            "column": column.name,
                            "declared_length": maximum_length,
                            "actual_length": actual_length,
                        }
                    )
            for constraint in model_table.foreign_key_constraints:
                remote_name = next(iter(constraint.elements)).column.table.name
                if remote_name not in source_metadata.tables:
                    continue
                valid = _foreign_key_validity(source_table, constraint, source_metadata)
                count = int(
                    connection.scalar(
                        select(func.count()).select_from(source_table).where(~valid)
                    )
                    or 0
                )
                if count:
                    orphan_rows.append(
                        {
                            "table": table_name,
                            "constraint": constraint.name,
                            "parent_table": remote_name,
                            "rows": count,
                        }
                    )
    return {
        "sqlite_quick_check": check,
        "source_tables": len(source_tables),
        "model_tables": len(model_tables),
        "source_tables_without_models": missing_models,
        "model_tables_without_source": missing_source,
        "orphan_rows": orphan_rows,
        "length_violations": length_violations,
    }


def _ensure_empty_target(connection: Connection) -> None:
    inspector = inspect(connection)
    occupied: list[str] = []
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        count = int(connection.scalar(text(f'SELECT COUNT(*) FROM "{table_name}"')) or 0)
        if count:
            occupied.append(f"{table_name}={count}")
    if occupied:
        raise RuntimeError("PostgreSQL target is not empty: " + ", ".join(occupied))


def _reset_postgres_sequences(connection: Connection) -> int:
    reset = 0
    for table in db.metadata.sorted_tables:
        for column in table.primary_key.columns:
            if not column.type.python_type is int:
                continue
            sequence = connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            )
            if not sequence:
                continue
            maximum = connection.scalar(select(func.max(column)))
            value = int(maximum) if maximum is not None else 1
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :value, :is_called)"),
                {"sequence": sequence, "value": value, "is_called": maximum is not None},
            )
            reset += 1
    return reset


def _stamp_alembic(project_root: Path, target_uri: str) -> None:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", target_uri.replace("%", "%%"))
    command.stamp(config, "head")


def migrate(
    sqlite_path: Path,
    target_uri: str,
    project_root: Path,
    *,
    skip_orphans: bool = False,
) -> dict[str, object]:
    source_engine = _sqlite_engine(sqlite_path)
    target_engine = _postgres_engine(target_uri)
    try:
        report = preflight(source_engine)
        if report["sqlite_quick_check"] != "ok":
            raise RuntimeError("SQLite quick_check failed")
        if report["source_tables_without_models"] or report["model_tables_without_source"]:
            raise RuntimeError("SQLite and model table sets differ; inspect the preflight report")
        if report["length_violations"]:
            raise RuntimeError("SQLite values exceed model column lengths; inspect the preflight report")
        if report["orphan_rows"] and not skip_orphans:
            raise RuntimeError(
                "Foreign-key orphans exist; repair them or explicitly use --skip-orphans"
            )

        source_metadata = _source_metadata(source_engine)
        with target_engine.connect() as target_connection:
            _ensure_empty_target(target_connection)
        db.metadata.create_all(target_engine)

        migrated_counts: dict[str, int] = {}
        skipped_counts: dict[str, int] = {}
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            for model_table in db.metadata.sorted_tables:
                source_table = source_metadata.tables[model_table.name]
                condition = _table_filter(source_table, model_table, source_metadata)
                total = int(source_connection.scalar(select(func.count()).select_from(source_table)) or 0)
                query = select(source_table)
                if skip_orphans and condition is not None:
                    query = query.where(condition)
                if source_table.primary_key.columns:
                    query = query.order_by(*source_table.primary_key.columns)
                result = source_connection.execute(query)
                inserted = 0
                while True:
                    batch = result.mappings().fetchmany(1000)
                    if not batch:
                        break
                    rows = [
                        {column.name: row[column.name] for column in model_table.columns if column.name in row}
                        for row in batch
                    ]
                    target_connection.execute(model_table.insert(), rows)
                    inserted += len(rows)
                migrated_counts[model_table.name] = inserted
                if inserted != total:
                    skipped_counts[model_table.name] = total - inserted
            sequences = _reset_postgres_sequences(target_connection)

        with target_engine.connect() as connection:
            mismatches = {}
            for table in db.metadata.sorted_tables:
                actual = int(connection.scalar(select(func.count()).select_from(table)) or 0)
                if actual != migrated_counts[table.name]:
                    mismatches[table.name] = {
                        "expected": migrated_counts[table.name],
                        "actual": actual,
                    }
            if mismatches:
                raise RuntimeError(f"PostgreSQL row-count verification failed: {mismatches}")
        _stamp_alembic(project_root.resolve(), target_uri)
        report.update(
            {
                "migrated_rows": sum(migrated_counts.values()),
                "migrated_tables": len(migrated_counts),
                "skipped_rows": skipped_counts,
                "reset_sequences": sequences,
                "row_count_verification": "ok",
            }
        )
        return report
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--target")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--skip-orphans", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        engine = _sqlite_engine(args.sqlite)
        try:
            print(json.dumps(preflight(engine), ensure_ascii=False, indent=2))
        finally:
            engine.dispose()
        return 0
    if not args.target:
        parser.error("--target is required unless --preflight-only is used")
    result = migrate(
        args.sqlite,
        args.target,
        args.project_root,
        skip_orphans=args.skip_orphans,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
