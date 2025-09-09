from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from .models import User
from werkzeug.middleware.proxy_fix import ProxyFix
import os

from .models import db
login_manager = LoginManager()

def create_app():
    app = Flask(__name__, static_folder='./static/')
    app.config['SECRET_KEY'] = 'your_secret_key'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'  # SQLiteデータベース
    db.init_app(app)
    login_manager.login_view = "auth.login"  # ログインページのエンドポイント
    login_manager.init_app(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    
    # ユーザーログインの管理
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))


    app.secret_key = 'test'

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

    from .tools.leave_mgr import leave_mgr_bp
    app.register_blueprint(leave_mgr_bp)

    return app







