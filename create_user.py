# create_user.py

from getpass import getpass
from app import create_app
from app.models import db, User
from werkzeug.security import generate_password_hash

def main():
    app = create_app()
    with app.app_context():
        print("=== DSTT ユーザー追加ツール ===")
        
        # テーブルが存在しない場合は作成
        try:
            # テーブルの存在確認のためのクエリ
            User.query.first()
        except Exception as e:
            print("データベーステーブルが存在しません。作成しています...")
            db.create_all()
            print("データベーステーブルを作成しました。")

        while True:
            username = input("🆔 ユーザーID を入力してください: ").strip()
            if not username:
                print("❌ ユーザーIDは空にできません。")
                continue

            # すでに存在チェック
            existing = User.query.filter_by(username=username).first()
            if existing:
                print("❌ そのユーザーIDはすでに存在します。")
                continue
            break

        # 日本語名の入力を追加
        while True:
            name = input("👤 日本語名を入力してください（例：田中太郎）: ").strip()
            if not name:
                print("❌ 日本語名は空にできません。")
                continue
            break

        while True:
            password = getpass("🔐 パスワードを入力してください: ")
            confirm = getpass("🔐 パスワードを再入力してください: ")
            if not password:
                print("❌ パスワードは空にできません。")
            elif password != confirm:
                print("❌ パスワードが一致しません。もう一度入力してください。")
            else:
                break

        hashed = generate_password_hash(password)
        new_user = User(username=username, password_hash=hashed, name=name)
        db.session.add(new_user)
        db.session.commit()

        print(f"✅ ユーザー「{username}」（{name}）を登録しました。")

if __name__ == "__main__":
    main()