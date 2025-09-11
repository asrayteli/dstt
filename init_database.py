# init_database.py
from app import create_app
from app.models import db

def init_database():
    app = create_app()
    with app.app_context():
        print("データベースを初期化しています...")
        
        # 全テーブルを削除して再作成
        db.drop_all()
        db.create_all()
        
        print("データベースの初期化が完了しました。")
        print("create_user.py を実行して新しいユーザーを作成してください。")

if __name__ == "__main__":
    init_database()