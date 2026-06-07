from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from .models import User
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import secrets
import time
from datetime import date
from pathlib import Path
from .navigation import NAV_ITEMS
from .versioning import calculate_repo_version

from .models import UserActivityLog, db
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


def _tool_info_for_path(path: str) -> tuple[str | None, str | None]:
    if _path_matches_prefix(path, "/tools/user_management"):
        return "user_management", "管理者ページ"
    for item in sorted(NAV_ITEMS, key=lambda nav_item: len(nav_item["href"]), reverse=True):
        if _path_matches_prefix(path, item["href"]):
            return item.get("key"), item.get("label")
    return None, None


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


def _seed_tool_categories():
    """カテゴリとツール設定の初期化・補完。

    - 初回起動: デフォルトカテゴリとツール割り当てを作成
    - 毎回: NAV_ITEMSにあるがToolSettingsに未登録のツールを自動補完
    """
    from .models import ToolCategory, ToolSettings
    from .navigation import NAV_ITEMS
    from .access_control import TOOL_ACCESS_CATEGORIES

    if ToolCategory.query.first() is None:
        DEFAULT_CATEGORIES = [
            ("大新東ツール", [
                "shiftersync", "monthly_generator", "subject_analysis_tool",
                "pluslist", "siteplus", "to_bell", "leave_mgr", "bus_pricing",
            ]),
            ("ファイル操作", [
                "rename", "compress", "csvtool", "pdf_power", "share",
            ]),
            ("計算ツール", [
                "datecalc", "calc", "workday",
            ]),
            ("画像・デザイン", [
                "color_extract", "powerstamp", "power_imager", "power_flow",
            ]),
            ("セキュリティ", [
                "password_tool",
            ]),
            ("その他", [
                "car_inspe", "powervote",
            ]),
        ]

        for i, (name, tool_keys) in enumerate(DEFAULT_CATEGORIES):
            cat = ToolCategory(name=name, sort_order=i)
            db.session.add(cat)
            db.session.flush()
            for j, key in enumerate(tool_keys):
                ts = db.session.get(ToolSettings, key)
                if not ts:
                    ts = ToolSettings(tool_key=key)
                    db.session.add(ts)
                ts.category_id = cat.id
                ts.sort_order = j
                ts.access_type = TOOL_ACCESS_CATEGORIES.get(key, "public")

    KNOWN_DEFAULTS = {
        "bus_pricing": "大新東ツール",
        "to_bell": "大新東ツール",
        "health_check": "大新東ツール",
    }

    existing_keys = {ts.tool_key for ts in ToolSettings.query.all()}
    added = False
    for nav in NAV_ITEMS:
        key = nav.get("key", "")
        if key and key not in existing_keys:
            ts = ToolSettings(
                tool_key=key,
                access_type=TOOL_ACCESS_CATEGORIES.get(key, "public"),
            )
            default_cat_name = KNOWN_DEFAULTS.get(key)
            if default_cat_name:
                cat = ToolCategory.query.filter_by(name=default_cat_name).first()
                if cat:
                    max_order = db.session.query(
                        db.func.coalesce(db.func.max(ToolSettings.sort_order), 0)
                    ).filter(ToolSettings.category_id == cat.id).scalar()
                    ts.category_id = cat.id
                    ts.sort_order = max_order + 1
            db.session.add(ts)
            added = True

    if added:
        db.session.commit()
    else:
        db.session.commit()


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

        if "to_bell_projects" in inspector.get_table_names():
            project_cols = {c["name"] for c in inspector.get_columns("to_bell_projects")}
            if "calendar_only" not in project_cols:
                alters.append("ALTER TABLE to_bell_projects ADD COLUMN calendar_only BOOLEAN NOT NULL DEFAULT 0")
            if "pinned" not in project_cols:
                alters.append("ALTER TABLE to_bell_projects ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0")
            if "sort_order" not in project_cols:
                alters.append("ALTER TABLE to_bell_projects ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")

        if "to_bell_tasks" in inspector.get_table_names():
            task_cols = {c["name"] for c in inspector.get_columns("to_bell_tasks")}
            if "pinned" not in task_cols:
                alters.append("ALTER TABLE to_bell_tasks ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0")
            if "gcal_event_id" not in task_cols:
                alters.append("ALTER TABLE to_bell_tasks ADD COLUMN gcal_event_id VARCHAR(255)")
            if "gcal_synced_by" not in task_cols:
                alters.append("ALTER TABLE to_bell_tasks ADD COLUMN gcal_synced_by VARCHAR(80)")
            if "gcal_reminder_override" not in task_cols:
                alters.append("ALTER TABLE to_bell_tasks ADD COLUMN gcal_reminder_override JSON")

        if "to_bell_google_accounts" in inspector.get_table_names():
            gaccount_cols = {c["name"] for c in inspector.get_columns("to_bell_google_accounts")}
            if "calendar_sync_token" not in gaccount_cols:
                alters.append("ALTER TABLE to_bell_google_accounts ADD COLUMN calendar_sync_token TEXT")
            if "last_import_at" not in gaccount_cols:
                alters.append("ALTER TABLE to_bell_google_accounts ADD COLUMN last_import_at DATETIME")

        if "to_bell_user_settings" not in inspector.get_table_names():
            alters.append(
                "CREATE TABLE to_bell_user_settings ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  username VARCHAR(80) NOT NULL UNIQUE,"
                "  integrations JSON,"
                "  preferences JSON,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )

        if "to_bell_employee_links" not in inspector.get_table_names():
            alters.append(
                "CREATE TABLE to_bell_employee_links ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  target_type VARCHAR(16) NOT NULL,"
                "  target_id INTEGER NOT NULL,"
                "  employee_id INTEGER NOT NULL,"
                "  created_by VARCHAR(80) NOT NULL,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  UNIQUE(target_type, target_id, employee_id)"
                ")"
            )

        if "to_bell_site_links" not in inspector.get_table_names():
            alters.append(
                "CREATE TABLE to_bell_site_links ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  target_type VARCHAR(16) NOT NULL,"
                "  target_id INTEGER NOT NULL,"
                "  site_row_id INTEGER NOT NULL,"
                "  created_by VARCHAR(80) NOT NULL,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  UNIQUE(target_type, target_id, site_row_id)"
                ")"
            )

        if "to_bell_external_files" not in inspector.get_table_names():
            alters.append(
                "CREATE TABLE to_bell_external_files ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  target_type VARCHAR(16) NOT NULL,"
                "  target_id INTEGER NOT NULL,"
                "  provider VARCHAR(32) NOT NULL DEFAULT 'filepost',"
                "  share_id VARCHAR(80) NOT NULL,"
                "  file_name VARCHAR(255) NOT NULL,"
                "  file_size BIGINT NOT NULL DEFAULT 0,"
                "  mime_type VARCHAR(120),"
                "  private_url VARCHAR(500) NOT NULL,"
                "  download_token VARCHAR(120),"
                "  note TEXT NOT NULL DEFAULT '',"
                "  uploaded_by VARCHAR(80) NOT NULL,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )

        if "health_check_records" in inspector.get_table_names():
            hc_cols = {c["name"] for c in inspector.get_columns("health_check_records")}
            if "birth_date" not in hc_cols:
                alters.append("ALTER TABLE health_check_records ADD COLUMN birth_date DATE")
            if "nasva_reservation_date" not in hc_cols:
                alters.append("ALTER TABLE health_check_records ADD COLUMN nasva_reservation_date DATE")
            if "nasva_exam_date" not in hc_cols:
                alters.append("ALTER TABLE health_check_records ADD COLUMN nasva_exam_date DATE")

        if "health_check_attachments" in inspector.get_table_names():
            hca_cols = {c["name"] for c in inspector.get_columns("health_check_attachments")}
            if "category" not in hca_cols:
                alters.append("ALTER TABLE health_check_attachments ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'health'")
            if "title" not in hca_cols:
                alters.append("ALTER TABLE health_check_attachments ADD COLUMN title VARCHAR(120)")

        if "vehicle_inspection_records" in inspector.get_table_names():
            vehicle_inspection_cols = {c["name"] for c in inspector.get_columns("vehicle_inspection_records")}
            if "model_type" in vehicle_inspection_cols:
                alters.append("ALTER TABLE vehicle_inspection_records DROP COLUMN model_type")

        tables = set(inspector.get_table_names())

        if "tool_categories" not in tables:
            alters.append(
                "CREATE TABLE tool_categories ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name VARCHAR(100) NOT NULL UNIQUE,"
                "  sort_order INTEGER NOT NULL DEFAULT 0,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )

        if "tool_settings" not in tables:
            alters.append(
                "CREATE TABLE tool_settings ("
                "  tool_key VARCHAR(80) PRIMARY KEY,"
                "  is_visible BOOLEAN NOT NULL DEFAULT 1,"
                "  access_type VARCHAR(20) NOT NULL DEFAULT 'public',"
                "  category_id INTEGER REFERENCES tool_categories(id),"
                "  sort_order INTEGER NOT NULL DEFAULT 0,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        else:
            ts_cols = {c["name"] for c in inspector.get_columns("tool_settings")}
            if "access_type" not in ts_cols:
                alters.append(
                    "ALTER TABLE tool_settings ADD COLUMN access_type VARCHAR(20) NOT NULL DEFAULT 'public'"
                )
                from .access_control import TOOL_ACCESS_CATEGORIES
                sensitive_keys = [k for k, v in TOOL_ACCESS_CATEGORIES.items() if v == "sensitive"]
                if sensitive_keys:
                    placeholders = ",".join(f"'{k}'" for k in sensitive_keys)
                    post_updates.append(
                        f"UPDATE tool_settings SET access_type = 'sensitive' WHERE tool_key IN ({placeholders})"
                    )

        if alters or post_updates:
            _run_schema_statements(alters, ignore_duplicates=True)
            _run_schema_statements(post_updates)

        # 旧来の固定管理者IDを is_admin=True に昇格
        from .access_control import ensure_legacy_admin_flag
        try:
            ensure_legacy_admin_flag()
        except Exception:
            db.session.rollback()

        _seed_tool_categories()

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
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)
    
    # ユーザーログインの管理
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.filter_by(username=user_id).first()

    @login_manager.request_loader
    def load_user_from_to_bell_share(req):
        # 共有トークンは ToBell 配下の経路でのみ有効（他ツールには波及させない）。
        if not (req.path or "").startswith("/tools/to_bell"):
            return None
        token = req.cookies.get("tb_share")
        if not token:
            return None
        from flask import g
        from .services.to_bell_service import resolve_share_token
        user = resolve_share_token(token)
        if user is not None:
            g.tobell_via_share_token = True
        return user
    @app.context_processor
    def inject_navigation():
        from .access_control import (
            get_accessible_nav_items,
            get_categorized_nav_items,
            is_admin_user,
        )
        from flask_login import current_user
        try:
            uid = current_user.username if current_user.is_authenticated else ""
        except Exception:
            uid = ""
        return {
            "app_navigation_items": get_accessible_nav_items(),
            "categorized_navigation": get_categorized_nav_items(),
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

    from .tools.bus_pricing import bus_pricing_bp
    app.register_blueprint(bus_pricing_bp)

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

    from .tools.health_check import health_check_bp
    app.register_blueprint(health_check_bp)

    from .tools.to_bell import to_bell_bp
    app.register_blueprint(to_bell_bp)

    from .tools.color_extract import color_extract_bp
    app.register_blueprint(color_extract_bp)

    from .tools.powerstamp import powerstamp_bp
    app.register_blueprint(powerstamp_bp)

    from .tools.powervote import powervote_bp
    app.register_blueprint(powervote_bp)

    from .tools.power_flow import power_flow_bp
    app.register_blueprint(power_flow_bp)

    from .tools.power_imager import power_imager_bp
    app.register_blueprint(power_imager_bp)

    # アクセス権管理（機密ツールに before_request を紐付け）
    from flask import request as _req
    from .access_control import enforce_tool_access

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
        "power_imager": "power_imager",
        "bus_pricing": "bus_pricing",
        "health_check": "health_check",
    }
    _EXEMPT_PATH_PREFIXES = (
        "/tools/shiftersync/download/",
        "/tools/shiftersync/cloudshift/view/",
        "/tools/shiftersync/cloudshift/edit/",
        # ViewPWA はスマホからのインストール・閲覧と Web Push 受信を前提とした
        # 共有先向けエンドポイント。リンクの保持が認可となるので未ログインで通す。
        "/tools/shiftersync/cloudshift/pwa/",
        "/tools/shiftersync/cloudshift/api/public/",
        "/tools/shiftersync/cloudshift/api/pwa/",
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
        path = _req.path or ""
        for pref in _EXEMPT_PATH_PREFIXES:
            if path.startswith(pref):
                return None
        return enforce_tool_access(tool_key)

    # 軽量 CSRF 対策: 個人情報を扱うツールの書き込み系 API に限り
    # Origin/Referer の同一オリジンを確認する（両方未設定なら通過）。
    from .security.request_guard import enforce_same_origin_for_mutations
    _SAME_ORIGIN_PATH_PREFIXES = (
        "/tools/bus_pricing/api/",
        "/tools/pluslist/api/",
        "/tools/siteplus/api/",
        "/tools/to_bell/api/",
        "/tools/health_check/api/",
    )

    @app.before_request
    def _enforce_same_origin_for_sensitive_apis():
        if app.config.get("TESTING") and not app.config.get("DSTT_ENFORCE_SAME_ORIGIN_IN_TESTS"):
            return None
        path = _req.path or ""
        if not any(path.startswith(p) for p in _SAME_ORIGIN_PATH_PREFIXES):
            return None
        return enforce_same_origin_for_mutations()

    @app.before_request
    def _run_to_bell_daily_cleanup():
        if app.config.get("TESTING") and not app.config.get("DSTT_RUN_TO_BELL_CLEANUP_IN_TESTS"):
            return None
        marker_path = Path(app.instance_path) / "to_bell_cleanup_last_run"
        today_text = date.today().isoformat()
        try:
            if marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == today_text:
                return None
            from .services.to_bell_service import cleanup_expired_records
            cleanup_expired_records()
            # 健診PLUSのリマインドを当日通知へ繰り上げる（due_at のローリング）
            try:
                from .services.to_bell_hooks import sweep_health_check_reminders
                sweep_health_check_reminders()
            except Exception:
                db.session.rollback()
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(today_text, encoding="utf-8")
        except Exception:
            db.session.rollback()
        return None

    @app.before_request
    def _mark_activity_start():
        from flask import g

        g.dstt_activity_started_at = time.perf_counter()
        return None

    @app.after_request
    def _apply_default_security_headers(response):
        # 全レスポンスに最低限のセキュリティヘッダを付与する。
        # setdefault を使い、個別ルートがより厳格な値（password_tool / share の
        # CSP・DENY 等）を設定済みの場合はそれを上書きしない。
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        # URL に共有トークンを含むツールがあるため、外部遷移時の Referer 漏洩を抑える。
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.after_request
    def _record_tool_activity(response):
        from flask import g, request as _request
        from flask_login import current_user as _current_user

        try:
            path = _request.path or ""
            if (
                not path.startswith("/tools/")
                or path.startswith("/tools/user_management/api/access-management")
                or _request.method in {"HEAD", "OPTIONS"}
            ):
                return response
            if not getattr(_current_user, "is_authenticated", False):
                return response
            tool_key, tool_label = _tool_info_for_path(path)
            if not tool_key:
                return response
            started = getattr(g, "dstt_activity_started_at", None)
            duration_ms = None
            if started is not None:
                duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            with db.engine.begin() as conn:
                conn.execute(
                    UserActivityLog.__table__.insert().values(
                        username=str(getattr(_current_user, "username", "") or ""),
                        tool_key=tool_key,
                        tool_label=tool_label,
                        endpoint=_request.endpoint,
                        method=_request.method,
                        path=path[:500],
                        status_code=int(response.status_code or 0),
                        duration_ms=duration_ms,
                        ip_address=_request.remote_addr,
                        user_agent=_request.user_agent.string,
                    )
                )
        except Exception:
            pass
        return response

    # 本番での暗号鍵必須化ガード（opt-in）
    if _env_bool("DSTT_REQUIRE_ENCRYPTION_KEY_ENV", False) and not app.config.get("TESTING"):
        from .security.file_crypto import EncryptionKeyMissingError, get_data_encryption_key
        try:
            get_data_encryption_key()
        except EncryptionKeyMissingError as exc:
            raise RuntimeError(str(exc))

    # DBスキーマの初期化（既存DBへのカラム追加含む）
    _ensure_access_control_schema(app)

    from .services.to_bell_push import init_to_bell_push_scheduler
    init_to_bell_push_scheduler(app)

    return app
