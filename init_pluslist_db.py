#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
社員名簿PLUS用データベース初期化スクリプト

新しいテーブル（offices, employees, upload_histories, edit_histories）を作成し、
初期営業所（千葉営業所）を登録します。
"""

from app import create_app
from app.models import db, Office

def init_pluslist_database():
    """社員名簿PLUS用のデータベースを初期化"""
    app = create_app()

    with app.app_context():
        print("社員名簿PLUS用テーブルを作成しています...")

        # 新しいテーブルのみを作成（既存のusersテーブルは影響を受けない）
        db.create_all()

        print("✓ テーブル作成完了")

        # 初期営業所データを作成
        existing_office = Office.query.filter_by(office_code='112010').first()
        if not existing_office:
            chiba_office = Office(
                office_code='112010',
                office_name='千葉営業所',
                created_by='3243012'  # 管理者
            )
            db.session.add(chiba_office)
            db.session.commit()
            print("✓ 初期営業所（千葉営業所: 112010）を作成しました")
        else:
            print("✓ 初期営業所は既に存在します")

        print("\n初期化が完了しました！")
        print("管理者ID: 3243012")
        print("初期営業所: 112010 (千葉営業所)")

if __name__ == '__main__':
    init_pluslist_database()
