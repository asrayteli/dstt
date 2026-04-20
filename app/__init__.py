from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import inspect, text
from .models import User
from werkzeug.middleware.proxy_fix import ProxyFix
import os
from dotenv import load_dotenv
from .navigation import NAV_ITEMS

load_dotenv()

from .models import db
login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address, default_limits=["300 per day", "60 per hour"])


def _ensure_access_control_schema(app):
    """既存DBにアクセス権管理用のテーブル/カラムを自動追加する。"""
    with app.app_context():
        db.create_all()

        inspector = inspect(db.engine)
        alters = []

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

        if alters:
            with db.engine.begin() as conn:
                for sql in alters:
                    conn.execute(text(sql))

        # 旧来の固定管理者IDを is_admin=True に昇格
        from .access_control import ensure_legacy_admin_flag
        try:
            ensure_legacy_admin_flag()
        except Exception:
            db.session.rollback()

def create_app(test_config=None):
    app = Flask(__name__, static_folder='./static/')

    secret_key = os.environ.get('DSTT_SECRET_KEY', '')
    if not secret_key:
        raise RuntimeError(
            "DSTT_SECRET_KEY が設定されていません。"
            ".env ファイルに DSTT_SECRET_KEY を設定してください。"
        )
    app.config['SECRET_KEY'] = secret_key
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DSTT_DATABASE_URI', 'sqlite:///users.db')

    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    limiter.init_app(app)
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
        return {
            "app_navigation_items": get_accessible_nav_items(),
            "all_navigation_items": NAV_ITEMS,
            "current_user_is_admin": is_admin_user(),
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

    from .tools.workday import workday_bp
    app.register_blueprint(workday_bp)

    from .tools.pdf_power import pdf_power_bp
    app.register_blueprint(pdf_power_bp)

    from .tools.share import share_bp
    app.register_blueprint(share_bp)

    from .tools.car_inspe import car_inspe_bp
    app.register_blueprint(car_inspe_bp)

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

    # DBスキーマの初期化（既存DBへのカラム追加含む）
    _ensure_access_control_schema(app)

    return app
