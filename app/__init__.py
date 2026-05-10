from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from .models import User
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import secrets
from pathlib import Path
from .navigation import NAV_ITEMS
from .versioning import calculate_repo_version

from .models import db
login_manager = LoginManager()

DEFAULT_PAGE_TITLE = "DSTT - DaishintoTools"


def _format_page_title(tool_name: str | None) -> str:
    if not tool_name:
        return DEFAULT_PAGE_TITLE
    return f"DSTT - {tool_name}"


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _tool_name_for_path(path: str) -> str | None:
    special_paths = (
        ("/tools/shiftersync/cloudshift", "CloudShift"),
        ("/tools/shiftersync/check", "ShifterSync"),
        ("/tools/shiftersync/create", "ShifterSync"),
        ("/tools/shiftersync/upload", "ShifterSync"),
        ("/tools/shiftersync/calendar", "ShifterSync"),
        ("/admin/announcements", "お知らせ管理"),
        ("/admin", "管理画面"),
    )
    for prefix, name in special_paths:
        if _path_matches_prefix(path, prefix):
            return name

    for item in sorted(NAV_ITEMS, key=lambda nav_item: len(nav_item["href"]), reverse=True):
        if _path_matches_prefix(path, item["href"]):
            return item["label"]
    return None


def resolve_page_title() -> str:
    from flask import request

    path = request.path or "/"
    if path == "/":
        return DEFAULT_PAGE_TITLE
    return _format_page_title(_tool_name_for_path(path))


def _is_duplicate_schema_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "duplicate column" in message
        or "already exists" in message
        or "duplicate column name" in message
    )


def _run_schema_statements(statements: list[str], *, ignore_duplicates: bool = False) -> None:
    for sql in statements:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
        except SQLAlchemyError as exc:
            if ignore_duplicates and _is_duplicate_schema_error(exc):
                continue
            raise


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_or_create_local_secret(app: Flask) -> str:
    secret_path = Path(app.instance_path) / "secret_key"
    if secret_path.exists():
        secret_key = secret_path.read_text(encoding="utf-8").strip()
        if secret_key:
            return secret_key

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_key = secrets.token_urlsafe(48)
    secret_path.write_text(secret_key, encoding="utf-8")
    return secret_key


def _resolve_secret_key(app: Flask, test_config=None) -> str:
    if test_config and test_config.get("SECRET_KEY"):
        return str(test_config["SECRET_KEY"])

    secret_key = (os.environ.get("DSTT_SECRET_KEY") or "").strip()
    if secret_key:
        return secret_key

    return _load_or_create_local_secret(app)


def _resolve_database_uri(test_config=None) -> str:
    if test_config and test_config.get("SQLALCHEMY_DATABASE_URI"):
        return str(test_config["SQLALCHEMY_DATABASE_URI"])
    return os.environ.get("DSTT_DATABASE_URI", "sqlite:///users.db")


def _ensure_access_control_schema(app):
    """既存DBにアクセス権管理用のテーブル/カラムを自動追加する。"""
    with app.app_context():
        db.create_all()

        inspector = inspect(db.engine)
        alters = []
        post_updates = []

        user_columns = {c["name"] for c in inspector.get_columns("users")}
        if "is_admin" not in user_columns:
            alters.append("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
        if "branch_id" not in user_columns:
            alters.append("ALTER TABLE users ADD COLUMN branch_id INTEGER")
        if "office_id" not in user_columns:
            alters.append("ALTER TABLE users ADD COLUMN office_id INTEGER")
        if "department_id" not in user_columns:
            alters.append("ALTER TABLE users ADD COLUMN department_id INTEGER")

        if "access_branches" in inspector.get_table_names():
            branch_cols = {c["name"] for c in inspector.get_columns("access_branches")}
            if "code" not in branch_cols:
                alters.append("ALTER TABLE access_branches ADD COLUMN code VARCHAR(20)")

        if "access_offices" in inspector.get_table_names():
            office_cols = {c["name"] for c in inspector.get_columns("access_offices")}
            if "code" not in office_cols:
                alters.append("ALTER TABLE access_offices ADD COLUMN code VARCHAR(20)")

        if "sites" in inspector.get_table_names():
            site_cols = {c["name"] for c in inspector.get_columns("sites")}
            if "office_code" not in site_cols:
                alters.append("ALTER TABLE sites ADD COLUMN office_code VARCHAR(20)")

        if "site_contract_master" in inspector.get_table_names():
            contract_cols = {c["name"] for c in inspector.get_columns("site_contract_master")}
            if "vehicle_number" not in contract_cols:
                alters.append("ALTER TABLE site_contract_master ADD COLUMN vehicle_number VARCHAR(40)")
            if "vehicle_number_updated_by" not in contract_cols:
                alters.append("ALTER TABLE site_contract_master ADD COLUMN vehicle_number_updated_by VARCHAR(80)")
            if "vehicle_number_updated_at" not in contract_cols:
                alters.append("ALTER TABLE site_contract_master ADD COLUMN vehicle_number_updated_at DATETIME")

        if "cloudshift_projects" in inspector.get_table_names():
            project_cols = {c["name"] for c in inspector.get_columns("cloudshift_projects")}
            if "site_manager_id" not in project_cols:
                alters.append("ALTER TABLE cloudshift_projects ADD COLUMN site_manager_id VARCHAR(20)")
            if "site_manager_name" not in project_cols:
                alters.append("ALTER TABLE cloudshift_projects ADD COLUMN site_manager_name VARCHAR(200)")
            if "account_shares" not in project_cols:
                alters.append("ALTER TABLE cloudshift_projects ADD COLUMN account_shares JSON")
                post_updates.append("UPDATE cloudshift_projects SET account_shares = '{}' WHERE account_shares IS NULL")
            if "assist" not in project_cols:
                alters.append("ALTER TABLE cloudshift_projects ADD COLUMN assist JSON")
                post_updates.append("UPDATE cloudshift_projects SET assist = '{}' WHERE assist IS NULL")
            if "extra_data" not in project_cols:
                alters.append("ALTER TABLE cloudshift_projects ADD COLUMN extra_data JSON")
                post_updates.append("UPDATE cloudshift_projects SET extra_data = '{}' WHERE extra_data IS NULL")

        if "cloudshift_months" in inspector.get_table_names():
            month_cols = {c["name"] for c in inspector.get_columns("cloudshift_months")}
            if "draft_entries_per_day" not in month_cols:
                alters.append("ALTER TABLE cloudshift_months ADD COLUMN draft_entries_per_day JSON")
                post_updates.append("UPDATE cloudshift_months SET draft_entries_per_day = '{}' WHERE draft_entries_per_day IS NULL")
            if "revision" not in month_cols:
                alters.append("ALTER TABLE cloudshift_months ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            if "revision_snapshots" not in month_cols:
                alters.append("ALTER TABLE cloudshift_months ADD COLUMN revision_snapshots JSON")
                post_updates.append("UPDATE cloudshift_months SET revision_snapshots = '{}' WHERE revision_snapshots IS NULL")

        if "cloudshift_history" in inspector.get_table_names():
            history_cols = {c["name"] for c in inspector.get_columns("cloudshift_history")}
            if "changes" not in history_cols:
                alters.append("ALTER TABLE cloudshift_history ADD COLUMN changes JSON")
                post_updates.append("UPDATE cloudshift_history SET changes = '[]' WHERE changes IS NULL")
            if "payload" not in history_cols:
                alters.append("ALTER TABLE cloudshift_history ADD COLUMN payload JSON")
                post_updates.append("UPDATE cloudshift_history SET payload = '{}' WHERE payload IS NULL")

        if "vehicle_inspection_records" in inspector.get_table_names():
            vehicle_inspection_cols = {c["name"] for c in inspector.get_columns("vehicle_inspection_records")}
            if "model_type" in vehicle_inspection_cols:
                alters.append("ALTER TABLE vehicle_inspection_records DROP COLUMN model_type")

        if alters or post_updates:
            _run_schema_statements(alters, ignore_duplicates=True)
            _run_schema_statements(post_updates)

        # 旧来の固定管理者IDを is_admin=True に昇格
        from .access_control import ensure_legacy_admin_flag
        try:
            ensure_legacy_admin_flag()
        except Exception:
            db.session.rollback()

def create_app(test_config=None):
    app = Flask(__name__, static_folder='./static/')
    app_version = os.environ.get('DSTT_APP_VERSION')
    if app_version:
        app.config['APP_VERSION'] = app_version
    else:
        app.config['APP_VERSION'] = calculate_repo_version(Path(app.root_path).parent)
    app.config['SECRET_KEY'] = _resolve_secret_key(app, test_config)
    app.config['SQLALCHEMY_DATABASE_URI'] = _resolve_database_uri(test_config)
    app.config['ALLOW_SELF_REGISTRATION'] = _env_bool('DSTT_ALLOW_SELF_REGISTRATION', False)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('DSTT_SESSION_COOKIE_SAMESITE', 'Lax')
    app.config['SESSION_COOKIE_SECURE'] = _env_bool(
        'DSTT_SESSION_COOKIE_SECURE',
        not bool(test_config and test_config.get('TESTING')),
    )
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = os.environ.get('DSTT_REMEMBER_COOKIE_SAMESITE', 'Lax')
    app.config['REMEMBER_COOKIE_SECURE'] = app.config['SESSION_COOKIE_SECURE']
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"  # ログインページのエンドポイント
    login_manager.init_app(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    
    # ユーザーログインの管理
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.filter_by(username=user_id).first()
    @app.context_processor
    def inject_navigation():
        from .access_control import (
            get_accessible_nav_items,
            is_admin_user,
        )
        from flask_login import current_user
        try:
            uid = current_user.username if current_user.is_authenticated else ""
        except Exception:
            uid = ""
        return {
            "app_navigation_items": get_accessible_nav_items(),
            "all_navigation_items": NAV_ITEMS,
            "current_user_is_admin": is_admin_user(),
            "current_user_id": uid,
            "app_version": app.config.get("APP_VERSION", "v1.0.0"),
            "default_page_title": resolve_page_title,
        }

    # トップページ
    from .routes import main
    app.register_blueprint(main)

    from .auth import auth_bp
    app.register_blueprint(auth_bp)

    # 各ツールBlueprint
    from .tools.datecalc import datecalc_bp
    app.register_blueprint(datecalc_bp)

    from .tools.calc import calc_bp
    app.register_blueprint(calc_bp)

    from .tools.rename import rename_bp
    app.register_blueprint(rename_bp)
    
    from .tools.compress import compress_bp
    app.register_blueprint(compress_bp)

    from .tools.csvtool import csvtool_bp
    app.register_blueprint(csvtool_bp)

    from .tools.password_tool import password_tool_bp
    app.register_blueprint(password_tool_bp)

    try:
        from .tools.workday import workday_bp
        app.register_blueprint(workday_bp)
    except ModuleNotFoundError:
        if not app.config.get("TESTING"):
            raise

    from .tools.pdf_power import pdf_power_bp
    app.register_blueprint(pdf_power_bp)

    try:
        from .tools.share import share_bp, init_share_cleanup
        app.register_blueprint(share_bp)
        init_share_cleanup(app)
    except ModuleNotFoundError:
        if not app.config.get("TESTING"):
            raise

    try:
        from .tools.car_inspe import car_inspe_bp, warmup_tesseract
        app.register_blueprint(car_inspe_bp)
        # Tesseract をバックグラウンドで予熱し、初回 OCR の体感速度を改善する。
        if not app.config.get("TESTING"):
            import threading as _threading
            _threading.Thread(
                target=warmup_tesseract,
                name="car_inspe_tesseract_warmup",
                daemon=True,
            ).start()
    except ModuleNotFoundError:
        if not app.config.get("TESTING"):
            raise

    from .tools.shiftersync import shiftersync_bp
    app.register_blueprint(shiftersync_bp)

    from .tools.cloudshift import cloudshift_bp
    app.register_blueprint(cloudshift_bp)

    from .tools.leave_mgr import leave_mgr_bp
    app.register_blueprint(leave_mgr_bp)

    from .tools.user_management import user_management_bp
    app.register_blueprint(user_management_bp)

    from .tools.monthly_generator import monthly_generator_bp
    app.register_blueprint(monthly_generator_bp)

    from .tools.subject_analysis_tool import subject_analysis_tool_bp
    app.register_blueprint(subject_analysis_tool_bp)

    from .tools.pluslist import pluslist_bp
    app.register_blueprint(pluslist_bp)

    from .tools.siteplus import siteplus_bp
    app.register_blueprint(siteplus_bp)

    from .tools.color_extract import color_extract_bp
    app.register_blueprint(color_extract_bp)

    from .tools.powerstamp import powerstamp_bp
    app.register_blueprint(powerstamp_bp)

    from .tools.powervote import powervote_bp
    app.register_blueprint(powervote_bp)

    from .tools.power_flow import power_flow_bp
    app.register_blueprint(power_flow_bp)

    # アクセス権管理（機密ツールに before_request を紐付け）
    from flask import request as _req
    from .access_control import TOOL_ACCESS_CATEGORIES, enforce_tool_access

    # Blueprint毎のアクセス制御除外パスプレフィックス。
    # トークン共有等、ログインしていない外部ユーザーが利用する経路を除外する。
    _BP_TO_TOOL_KEY = {
        "leave_mgr": "leave_mgr",
        "pluslist": "pluslist",
        "siteplus": "siteplus",
        "shiftersync": "shiftersync",
        # CloudShiftはShifterSyncのサブ機能なので、ShifterSync権限で判定する
        "cloudshift": "shiftersync",
        "subject_analysis_tool": "subject_analysis_tool",
    }
    _EXEMPT_PATH_PREFIXES = (
        "/tools/shiftersync/download/",
        "/tools/shiftersync/cloudshift/view/",
        "/tools/shiftersync/cloudshift/edit/",
        "/tools/shiftersync/cloudshift/api/public/",
    )

    @app.before_request
    def _enforce_sensitive_tool_access():
        endpoint = _req.endpoint or ""
        if "." not in endpoint:
            return None
        bp_name = endpoint.split(".", 1)[0]
        tool_key = _BP_TO_TOOL_KEY.get(bp_name)
        if not tool_key:
            return None
        if TOOL_ACCESS_CATEGORIES.get(tool_key) != "sensitive":
            return None
        path = _req.path or ""
        for pref in _EXEMPT_PATH_PREFIXES:
            if path.startswith(pref):
                return None
        return enforce_tool_access(tool_key)

    # 軽量 CSRF 対策: 個人情報を扱うツールの書き込み系 API に限り
    # Origin/Referer の同一オリジンを確認する（両方未設定なら通過）。
    from .security.request_guard import enforce_same_origin_for_mutations
    _SAME_ORIGIN_PATH_PREFIXES = (
        "/tools/pluslist/api/",
        "/tools/siteplus/api/",
    )

    @app.before_request
    def _enforce_same_origin_for_sensitive_apis():
        if app.config.get("TESTING") and not app.config.get("DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS"):
            return None
        path = _req.path or ""
        if not any(path.startswith(p) for p in _SAME_ORIGIN_PATH_PREFIXES):
            return None
        return enforce_same_origin_for_mutations()

    # 本番での暗号鍵必須化ガード（opt-in）
    if _env_bool("DSTT_REQUIRE_ENCRYPTION_KEY_ENV", False) and not app.config.get("TESTING"):
        from .security.file_crypto import EncryptionKeyMissingError, get_data_encryption_key
        try:
            get_data_encryption_key()
        except EncryptionKeyMissingError as exc:
            raise RuntimeError(str(exc))

    # DBスキーマの初期化（既存DBへのカラム追加含む）
    _ensure_access_control_schema(app)

    return app
