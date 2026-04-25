from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import create_app
from app.models import db
from app.tools.cloudshift import migrate_cloudshift_json_to_db


def _create_migration_app():
    try:
        return create_app()
    except ImportError:
        from flask import Flask

        root = Path.cwd()
        app = Flask(__name__, instance_path=str(root / "instance"))
        app.config["SECRET_KEY"] = os.environ.get("DSTT_SECRET_KEY", "cloudshift-migration")
        app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DSTT_DATABASE_URI", "sqlite:///users.db")
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import existing CloudShift JSON files into the configured DSTT database."
    )
    parser.add_argument("--dry-run", action="store_true", help="Count import targets without writing to the database.")
    args = parser.parse_args()

    app = _create_migration_app()
    with app.app_context():
        db.create_all()
        result = migrate_cloudshift_json_to_db(dry_run=args.dry_run)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
