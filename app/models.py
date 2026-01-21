from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=True, default='unknown')

    def __repr__(self):
        return f'<User {self.username}>'

    def get_id(self):
        # usernameを返すように修正（leave_mgr.pyでcurrent_user.usernameを使用しているため）
        return str(self.username)


# 社員名簿PLUS用モデル

class Office(db.Model):
    """営業所マスタ"""
    __tablename__ = 'offices'

    office_code = db.Column(db.String(20), primary_key=True)  # 所属コード（例: 112010）
    office_name = db.Column(db.String(100), nullable=False)  # 所属名称
    created_by = db.Column(db.String(80), nullable=False)  # 作成者
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # リレーション
    employees = db.relationship('Employee', back_populates='office', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Office {self.office_code}: {self.office_name}>'


class Employee(db.Model):
    """社員データ"""
    __tablename__ = 'employees'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_number = db.Column(db.String(20), unique=True, nullable=False, index=True)  # 社員番号
    office_code = db.Column(db.String(20), db.ForeignKey('offices.office_code'), nullable=False, index=True)

    # 基本情報
    office_name = db.Column(db.String(100))  # 所属名称
    employee_name = db.Column(db.String(100), nullable=False)  # 社員名称
    employee_kana = db.Column(db.String(100))  # 社員カナ名称
    employee_type = db.Column(db.String(50))  # 社員区分
    gender = db.Column(db.String(10))  # 性別

    # 原価・管理情報
    cost_code = db.Column(db.String(20))  # 原価コード
    cost_name = db.Column(db.String(100))  # 原価名称
    manager_number = db.Column(db.String(20))  # 管理担当社員番号
    manager_name = db.Column(db.String(100))  # 管理担当社員名

    # 住所情報
    postal_code = db.Column(db.String(20))  # 郵便番号
    address1 = db.Column(db.String(200))  # 住所1
    address2 = db.Column(db.String(200))  # 住所2
    mansion_name = db.Column(db.String(200))  # マンション名

    # 契約情報
    contract_code = db.Column(db.String(20))  # 契約コード
    company_name = db.Column(db.String(200))  # 法人名
    site_name = db.Column(db.String(200))  # 現場名
    job_title = db.Column(db.String(100))  # 職種名称

    # 日付情報
    birth_date = db.Column(db.Date)  # 生年月日
    hire_date = db.Column(db.Date)  # 入社日付
    retirement_date = db.Column(db.String(50))  # 退職日付（"？退職？"を含むためString）

    # 連絡先情報
    phone_number = db.Column(db.String(20))  # 電話番号
    mobile_phone = db.Column(db.String(20))  # 携帯電話（個人）

    # その他
    health_insurance = db.Column(db.String(20))  # 健康保険加入区分

    # システム情報
    is_deleted = db.Column(db.Boolean, default=False, index=True)  # 削除フラグ
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # リレーション
    office = db.relationship('Office', back_populates='employees')
    edit_histories = db.relationship('EditHistory', back_populates='employee',
                                    order_by='EditHistory.edited_at.desc()',
                                    cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Employee {self.employee_number}: {self.employee_name}>'

    def to_dict(self):
        """辞書形式に変換"""
        return {
            'id': self.id,
            'employee_number': self.employee_number,
            'office_code': self.office_code,
            'office_name': self.office_name,
            'employee_name': self.employee_name,
            'employee_kana': self.employee_kana,
            'employee_type': self.employee_type,
            'gender': self.gender,
            'cost_code': self.cost_code,
            'cost_name': self.cost_name,
            'manager_number': self.manager_number,
            'manager_name': self.manager_name,
            'postal_code': self.postal_code,
            'address1': self.address1,
            'address2': self.address2,
            'mansion_name': self.mansion_name,
            'contract_code': self.contract_code,
            'company_name': self.company_name,
            'site_name': self.site_name,
            'job_title': self.job_title,
            'birth_date': self.birth_date.isoformat() if self.birth_date else None,
            'hire_date': self.hire_date.isoformat() if self.hire_date else None,
            'retirement_date': self.retirement_date,
            'phone_number': self.phone_number,
            'mobile_phone': self.mobile_phone,
            'health_insurance': self.health_insurance,
            'is_deleted': self.is_deleted,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class UploadHistory(db.Model):
    """ファイルアップロード履歴"""
    __tablename__ = 'upload_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uploaded_by = db.Column(db.String(80), nullable=False)  # アップロードユーザーID
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    office_code = db.Column(db.String(20), nullable=False)  # 営業所コード
    filename = db.Column(db.String(255))  # ファイル名
    added_count = db.Column(db.Integer, default=0)  # 追加件数
    updated_count = db.Column(db.Integer, default=0)  # 更新件数
    deleted_count = db.Column(db.Integer, default=0)  # 削除件数
    total_count = db.Column(db.Integer, default=0)  # 総件数

    def __repr__(self):
        return f'<UploadHistory {self.id}: {self.filename} by {self.uploaded_by}>'


class EditHistory(db.Model):
    """Web編集履歴（最新50件のみ保持）"""
    __tablename__ = 'edit_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_number = db.Column(db.String(20), db.ForeignKey('employees.employee_number'), nullable=False, index=True)
    edited_by = db.Column(db.String(80), nullable=False)  # 編集者ユーザーID
    edited_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    action = db.Column(db.String(20), nullable=False)  # create / update / delete
    field_name = db.Column(db.String(100))  # 変更フィールド名
    old_value = db.Column(db.Text)  # 変更前の値
    new_value = db.Column(db.Text)  # 変更後の値

    # リレーション
    employee = db.relationship('Employee', back_populates='edit_histories')

    def __repr__(self):
        return f'<EditHistory {self.id}: {self.action} on {self.employee_number}>'