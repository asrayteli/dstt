#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
社員名簿PLUS用データベース初期化スクリプト

新しいテーブル（offices, employees, upload_histories, edit_histories,
salary_mappings, employee_salaries, salary_upload_histories）を作成し、
初期営業所（千葉営業所）と初期賃金マッピングを登録します。
"""

from app import create_app
from app.models import db, Office, SalaryMapping

# 初期賃金マッピングデータ
INITIAL_SALARY_MAPPINGS = [
    {'item_id': '115', 'display_name': '基本給', 'column_key': 'base_salary', 'sort_order': 0},
    {'item_id': '119', 'display_name': '専従手当', 'column_key': 'fulltime_allowance', 'sort_order': 1},
    {'item_id': '131', 'display_name': '家族手当', 'column_key': 'family_allowance', 'sort_order': 2},
    {'item_id': '439', 'display_name': '安全運転手当', 'column_key': 'safe_allowance', 'sort_order': 3},
    {'item_id': '422', 'display_name': '定額残業', 'column_key': 'overtime_allowance', 'sort_order': 4},
    {'item_id': '121', 'display_name': '管理責任者手当', 'column_key': 'manager_allowance', 'sort_order': 5},
    {'item_id': '1601', 'display_name': '社員会費', 'column_key': 'membership_fee', 'sort_order': 6},
    {'item_id': '133', 'display_name': '特別資格手当', 'column_key': 'qualification_allowance', 'sort_order': 7},
    {'item_id': '135', 'display_name': '調整手当', 'column_key': 'adjustment_allowance', 'sort_order': 8},
    {'item_id': '138', 'display_name': '職務推進', 'column_key': 'promotion_allowance', 'sort_order': 9},
    {'item_id': '401', 'display_name': '代務者手当', 'column_key': 'substitute_allowance', 'sort_order': 10},
    {'item_id': '431', 'display_name': '作業交代手当', 'column_key': 'shiftchange_allowance', 'sort_order': 11},
    {'item_id': '443', 'display_name': 'ベースアップ加算', 'column_key': 'baseup_addition', 'sort_order': 12},
]

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

        # 初期賃金マッピングデータを作成
        print("\n賃金マッピングを登録しています...")
        mappings_created = 0
        mappings_skipped = 0

        for mapping_data in INITIAL_SALARY_MAPPINGS:
            existing = SalaryMapping.query.filter_by(item_id=mapping_data['item_id']).first()
            if not existing:
                mapping = SalaryMapping(
                    item_id=mapping_data['item_id'],
                    display_name=mapping_data['display_name'],
                    column_key=mapping_data['column_key'],
                    sort_order=mapping_data['sort_order']
                )
                db.session.add(mapping)
                mappings_created += 1
            else:
                mappings_skipped += 1

        db.session.commit()
        print(f"✓ 賃金マッピング: {mappings_created}件追加、{mappings_skipped}件スキップ（既存）")

        print("\n初期化が完了しました！")
        print("管理者ID: 3243012")
        print("初期営業所: 112010 (千葉営業所)")
        print(f"賃金マッピング: {len(INITIAL_SALARY_MAPPINGS)}項目")

if __name__ == '__main__':
    init_pluslist_database()
