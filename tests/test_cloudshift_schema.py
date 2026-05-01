import sqlite3
import sys
from pathlib import Path

from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_PACKAGES = ROOT / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SITE_PACKAGES))


def test_create_app_upgrades_legacy_cloudshift_schema(tmp_path):
    db_path = tmp_path / "legacy-cloudshift.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE cloudshift_projects (
                id VARCHAR(24) PRIMARY KEY,
                owner_user_id VARCHAR(80) NOT NULL,
                title VARCHAR(200) NOT NULL,
                mode VARCHAR(20) NOT NULL,
                employee_number VARCHAR(40) NOT NULL DEFAULT '',
                site_row_id INTEGER,
                site_id VARCHAR(20),
                site_name VARCHAR(200),
                view_token VARCHAR(128) NOT NULL,
                edit_token VARCHAR(128) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            );
            CREATE TABLE cloudshift_months (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id VARCHAR(24) NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                capacity_enabled BOOLEAN NOT NULL DEFAULT 0,
                required_capacity INTEGER NOT NULL DEFAULT 0,
                entries_per_day JSON NOT NULL DEFAULT '{}',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            );
            CREATE TABLE cloudshift_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id VARCHAR(24) NOT NULL,
                timestamp VARCHAR(32) NOT NULL,
                editor_name VARCHAR(100) NOT NULL DEFAULT '',
                editor_type VARCHAR(30) NOT NULL DEFAULT '',
                action VARCHAR(60) NOT NULL DEFAULT '',
                month_key VARCHAR(7)
            );
            INSERT INTO cloudshift_projects (
                id, owner_user_id, title, mode, view_token, edit_token, created_at, updated_at
            ) VALUES (
                'legacyproject0000000001', 'owner01', 'Legacy', 'scene',
                'view-token', 'edit-token', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            INSERT INTO cloudshift_months (
                project_id, year, month, entries_per_day, created_at, updated_at
            ) VALUES (
                'legacyproject0000000001', 2026, 4, '{"1":[]}',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            INSERT INTO cloudshift_history (
                project_id, timestamp, editor_name, editor_type, action, month_key
            ) VALUES (
                'legacyproject0000000001', '2026-01-01T00:00:00Z',
                'Owner', 'owner', 'project_created', '2026-04'
            );
            """
        )

    from app import create_app
    from app.models import CloudShiftProject, db

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        }
    )

    with app.app_context():
        inspector = inspect(db.engine)
        project_cols = {c["name"] for c in inspector.get_columns("cloudshift_projects")}
        month_cols = {c["name"] for c in inspector.get_columns("cloudshift_months")}
        history_cols = {c["name"] for c in inspector.get_columns("cloudshift_history")}

        assert {"account_shares", "assist", "extra_data", "site_manager_id", "site_manager_name"} <= project_cols
        assert {"draft_entries_per_day", "revision", "revision_snapshots"} <= month_cols
        assert {"changes", "payload"} <= history_cols

        project = db.session.get(CloudShiftProject, "legacyproject0000000001")
        assert project is not None
        assert project.extra_data == {}
        assert project.months[0].draft_entries_per_day == {}
        assert project.months[0].revision == 1
