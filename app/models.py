from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

from .security.column_crypto import EncryptedText

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=True, default='unknown')

    # アクセス権管理（支店 > 営業所 > 担当）
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('access_branches.id'), nullable=True)
    office_id = db.Column(db.Integer, db.ForeignKey('access_offices.id'), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('access_departments.id'), nullable=True)

    branch = db.relationship('AccessBranch', foreign_keys=[branch_id])
    user_office = db.relationship('AccessOffice', foreign_keys=[office_id])
    department = db.relationship('AccessDepartment', foreign_keys=[department_id])

    tool_permissions = db.relationship(
        'UserToolPermission',
        back_populates='user',
        cascade='all, delete-orphan',
    )

    def __repr__(self):
        return f'<User {self.username}>'

    def get_id(self):
        # usernameを返すように修正（leave_mgr.pyでcurrent_user.usernameを使用しているため）
        return str(self.username)


class AccessBranch(db.Model):
    """支店マスタ（アクセス権管理用）"""
    __tablename__ = 'access_branches'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    offices = db.relationship(
        'AccessOffice',
        back_populates='branch',
        cascade='all, delete-orphan',
        order_by='AccessOffice.name',
    )

    def to_dict(self, include_children=False):
        data = {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_children:
            data['offices'] = [o.to_dict(include_children=True) for o in self.offices]
        return data


class AccessOffice(db.Model):
    """営業所マスタ（アクセス権管理用）"""
    __tablename__ = 'access_offices'
    __table_args__ = (
        db.UniqueConstraint('branch_id', 'name', name='uq_access_office_branch_name'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('access_branches.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    branch = db.relationship('AccessBranch', back_populates='offices')
    departments = db.relationship(
        'AccessDepartment',
        back_populates='office',
        cascade='all, delete-orphan',
        order_by='AccessDepartment.name',
    )

    def to_dict(self, include_children=False):
        data = {
            'id': self.id,
            'branch_id': self.branch_id,
            'name': self.name,
            'code': self.code,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_children:
            data['departments'] = [d.to_dict() for d in self.departments]
        return data


class AccessDepartment(db.Model):
    """担当マスタ（アクセス権管理用）"""
    __tablename__ = 'access_departments'
    __table_args__ = (
        db.UniqueConstraint('office_id', 'name', name='uq_access_department_office_name'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    office_id = db.Column(db.Integer, db.ForeignKey('access_offices.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    office = db.relationship('AccessOffice', back_populates='departments')

    def to_dict(self):
        return {
            'id': self.id,
            'office_id': self.office_id,
            'name': self.name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UserAccessibleOffice(db.Model):
    """ユーザーが主営業所以外に追加でアクセスできる営業所の紐付け"""
    __tablename__ = 'user_accessible_offices'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'office_id', name='uq_user_accessible_office'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    office_id = db.Column(db.Integer, db.ForeignKey('access_offices.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('extra_offices', cascade='all, delete-orphan'))
    office = db.relationship('AccessOffice')


class UserToolPermission(db.Model):
    """個別ツールアクセス権付与"""
    __tablename__ = 'user_tool_permissions'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'tool_key', name='uq_user_tool_permission'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tool_key = db.Column(db.String(80), nullable=False, index=True)
    granted_by = db.Column(db.String(80), nullable=True)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='tool_permissions')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'tool_key': self.tool_key,
            'granted_by': self.granted_by,
            'granted_at': self.granted_at.isoformat() if self.granted_at else None,
        }


class PasswordVault(db.Model):
    """Client-side encrypted password vault metadata."""

    __tablename__ = 'password_vaults'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)
    kdf_name = db.Column(db.String(50), nullable=False, default='PBKDF2-SHA-256')
    kdf_iterations = db.Column(db.Integer, nullable=False, default=600000)
    salt_b64 = db.Column(db.Text, nullable=False)
    check_nonce_b64 = db.Column(db.Text, nullable=False)
    check_ciphertext_b64 = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship(
        'User',
        backref=db.backref('password_vault', uselist=False, cascade='all, delete-orphan'),
    )
    items = db.relationship(
        'PasswordVaultItem',
        back_populates='vault',
        cascade='all, delete-orphan',
        order_by='PasswordVaultItem.updated_at.desc()',
    )


class PasswordVaultItem(db.Model):
    """Opaque encrypted vault item. The server never stores plaintext fields."""

    __tablename__ = 'password_vault_items'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vault_id = db.Column(db.Integer, db.ForeignKey('password_vaults.id'), nullable=False, index=True)
    schema_version = db.Column(db.Integer, nullable=False, default=1)
    nonce_b64 = db.Column(db.Text, nullable=False)
    ciphertext_b64 = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vault = db.relationship('PasswordVault', back_populates='items')


class GroupToolPermission(db.Model):
    """支店/営業所/担当による一括ツールアクセス権付与。
    branch_id, office_id, department_id のうちNULLは「任意」を意味する。
    ユーザーは、自身の支店/営業所(複数含む)/担当が、設定されたスコープを
    すべて満たす場合に、そのツールへのアクセスが許可される。"""
    __tablename__ = 'group_tool_permissions'
    __table_args__ = (
        db.UniqueConstraint(
            'tool_key', 'branch_id', 'office_id', 'department_id',
            name='uq_group_tool_permission',
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    tool_key = db.Column(db.String(80), nullable=False, index=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('access_branches.id'), nullable=True, index=True)
    office_id = db.Column(db.Integer, db.ForeignKey('access_offices.id'), nullable=True, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('access_departments.id'), nullable=True, index=True)
    granted_by = db.Column(db.String(80), nullable=True)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow)

    branch = db.relationship('AccessBranch')
    office = db.relationship('AccessOffice')
    department = db.relationship('AccessDepartment')

    def to_dict(self):
        return {
            'id': self.id,
            'tool_key': self.tool_key,
            'branch_id': self.branch_id,
            'office_id': self.office_id,
            'department_id': self.department_id,
            'branch_name': self.branch.name if self.branch else None,
            'office_name': self.office.name if self.office else None,
            'department_name': self.department.name if self.department else None,
            'granted_by': self.granted_by,
            'granted_at': self.granted_at.isoformat() if self.granted_at else None,
        }


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

    def calculate_age(self):
        """年齢を計算（例: 「45歳3ヶ月」）"""
        if not self.birth_date:
            return None

        today = date.today()
        delta = relativedelta(today, self.birth_date)
        years = delta.years
        months = delta.months

        if months > 0:
            return f"{years}歳{months}ヶ月"
        else:
            return f"{years}歳"

    def calculate_tenure(self):
        """入社年数を計算（例: 「5年7ヶ月」）"""
        if not self.hire_date:
            return None

        today = date.today()
        delta = relativedelta(today, self.hire_date)
        years = delta.years
        months = delta.months

        if months > 0:
            return f"{years}年{months}ヶ月"
        else:
            return f"{years}年"

    def calculate_age_months(self):
        """年齢を月数で返す（ソート用）"""
        if not self.birth_date:
            return None

        today = date.today()
        delta = relativedelta(today, self.birth_date)
        return delta.years * 12 + delta.months

    def calculate_tenure_months(self):
        """入社年数を月数で返す（ソート用）"""
        if not self.hire_date:
            return None

        today = date.today()
        delta = relativedelta(today, self.hire_date)
        return delta.years * 12 + delta.months

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
            'age': self.calculate_age(),
            'age_months': self.calculate_age_months(),
            'hire_date': self.hire_date.isoformat() if self.hire_date else None,
            'tenure': self.calculate_tenure(),
            'tenure_months': self.calculate_tenure_months(),
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
    # 個人情報を含みうるため AES-256-GCM で暗号化（既存平文レコードは自動で後方互換表示）
    old_value = db.Column(EncryptedText)
    new_value = db.Column(EncryptedText)

    # リレーション
    employee = db.relationship('Employee', back_populates='edit_histories')

    def __repr__(self):
        return f'<EditHistory {self.id}: {self.action} on {self.employee_number}>'


class SalaryMapping(db.Model):
    """賃金項目マッピング定義"""
    __tablename__ = 'salary_mappings'

    item_id = db.Column(db.String(20), primary_key=True)  # 項目ID（例: 115）
    display_name = db.Column(db.String(100), nullable=False)  # 表示名（例: 基本給）
    column_key = db.Column(db.String(50), nullable=False, unique=True)  # DBキー（例: base_salary）
    sort_order = db.Column(db.Integer, default=0)  # 表示順
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SalaryMapping {self.item_id}: {self.display_name}>'

    def to_dict(self):
        return {
            'item_id': self.item_id,
            'display_name': self.display_name,
            'column_key': self.column_key,
            'sort_order': self.sort_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class EmployeeSalary(db.Model):
    """社員賃金データ"""
    __tablename__ = 'employee_salaries'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_number = db.Column(db.String(20), nullable=False, index=True)  # 社員番号
    item_id = db.Column(db.String(20), db.ForeignKey('salary_mappings.item_id'), nullable=False)  # 項目ID
    amount = db.Column(db.Integer, nullable=False)  # 金額
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)  # アップロード日時
    uploaded_by = db.Column(db.String(80), nullable=False)  # アップロード者

    # リレーション
    mapping = db.relationship('SalaryMapping', backref='salaries')

    def __repr__(self):
        return f'<EmployeeSalary {self.employee_number}: {self.item_id}={self.amount}>'

    def to_dict(self):
        return {
            'id': self.id,
            'employee_number': self.employee_number,
            'item_id': self.item_id,
            'amount': self.amount,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'uploaded_by': self.uploaded_by
        }


class SalaryUploadHistory(db.Model):
    """賃金ファイルアップロード履歴"""
    __tablename__ = 'salary_upload_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uploaded_by = db.Column(db.String(80), nullable=False)  # アップロードユーザーID
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    filename = db.Column(db.String(255))  # ファイル名
    success_count = db.Column(db.Integer, default=0)  # 成功件数
    skip_count = db.Column(db.Integer, default=0)  # スキップ件数
    error_count = db.Column(db.Integer, default=0)  # エラー件数
    total_rows = db.Column(db.Integer, default=0)  # 総行数

    def __repr__(self):
        return f'<SalaryUploadHistory {self.id}: {self.filename} by {self.uploaded_by}>'


class Site(db.Model):
    __tablename__ = 'sites'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(db.String(5), unique=True, nullable=False, index=True)
    site_name = db.Column(db.String(200), nullable=False, index=True)
    site_manager_last = db.Column(db.String(100), nullable=False)
    site_manager_first = db.Column(db.String(100), nullable=False)
    site_manager_id = db.Column(db.String(20), nullable=False, index=True)
    site_register = db.Column(db.String(80), nullable=False, index=True)
    site_updater = db.Column(db.String(80), nullable=False, index=True)
    office_code = db.Column(db.String(20), nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    branches = db.relationship(
        'SiteBranch',
        back_populates='site',
        cascade='all, delete-orphan',
        order_by='SiteBranch.site_branch',
    )

    def manager_name(self):
        return f'{self.site_manager_last} {self.site_manager_first}'.strip()

    def to_dict(self, include_branches=True, include_inactive_branches=True):
        branches = []
        if include_branches:
            for branch in self.branches:
                if include_inactive_branches or branch.is_active:
                    branches.append(branch.to_dict())
        return {
            'id': self.id,
            'site_id': self.site_id,
            'site_name': self.site_name,
            'site_manager_last': self.site_manager_last,
            'site_manager_first': self.site_manager_first,
            'site_manager_id': self.site_manager_id,
            'site_manager_name': self.manager_name(),
            'site_register': self.site_register,
            'site_updater': self.site_updater,
            'office_code': self.office_code,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'branch_count': len(self.branches),
            'active_branch_count': len([branch for branch in self.branches if branch.is_active]),
            'branches': branches,
        }

    def __repr__(self):
        return f'<Site {self.site_id}: {self.site_name}>'


class SiteBranch(db.Model):
    __tablename__ = 'site_branches'

    __table_args__ = (
        db.UniqueConstraint('site_row_id', 'site_branch', name='uq_site_branches_site_row_id_site_branch'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_row_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    site_branch = db.Column(db.String(3), nullable=False, index=True)
    cloudshift_option_key = db.Column(db.String(20), nullable=False, index=True)
    site_register = db.Column(db.String(80), nullable=False, index=True)
    site_updater = db.Column(db.String(80), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    site = db.relationship('Site', back_populates='branches')

    def to_dict(self):
        return {
            'id': self.id,
            'site_row_id': self.site_row_id,
            'site_branch': self.site_branch,
            'cloudshift_option_key': self.cloudshift_option_key,
            'site_register': self.site_register,
            'site_updater': self.site_updater,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<SiteBranch {self.site_row_id}:{self.site_branch}>'


class SiteContractMaster(db.Model):
    __tablename__ = 'site_contract_master'

    contract_code = db.Column(db.String(20), primary_key=True)
    site_row_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    site_branch_row_id = db.Column(db.Integer, db.ForeignKey('site_branches.id'), index=True)
    site_id = db.Column(db.String(20), nullable=False, index=True)
    site_branch = db.Column(db.String(3), nullable=False, index=True)
    site_name = db.Column(db.String(200), nullable=False)
    site_manager_id = db.Column(db.String(20), nullable=False, index=True)
    site_manager_name = db.Column(db.String(200), nullable=False)
    segment = db.Column(db.String(20), index=True)
    cloudshift_option_key = db.Column(db.String(20), index=True)
    dedicated_employee_number = db.Column(db.String(20), index=True)
    dedicated_employee_name = db.Column(db.String(100))
    dedicated_updated_by = db.Column(db.String(80))
    dedicated_updated_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    source = db.Column(db.String(30), nullable=False, default='siteplus')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    site = db.relationship('Site', foreign_keys=[site_row_id], lazy='joined')
    branch = db.relationship('SiteBranch', foreign_keys=[site_branch_row_id], lazy='joined')

    def to_dict(self):
        return {
            'contract_code': self.contract_code,
            'site_row_id': self.site_row_id,
            'site_branch_row_id': self.site_branch_row_id,
            'site_id': self.site_id,
            'site_branch': self.site_branch,
            'site_name': self.site_name,
            'site_manager_id': self.site_manager_id,
            'site_manager_name': self.site_manager_name,
            'segment': self.segment,
            'cloudshift_option_key': self.cloudshift_option_key,
            'dedicated_employee_number': self.dedicated_employee_number,
            'dedicated_employee_name': self.dedicated_employee_name,
            'dedicated_updated_by': self.dedicated_updated_by,
            'dedicated_updated_at': self.dedicated_updated_at.isoformat() if self.dedicated_updated_at else None,
            'is_active': self.is_active,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<SiteContractMaster {self.contract_code}>'


class CloudShiftProject(db.Model):
    __tablename__ = 'cloudshift_projects'

    id = db.Column(db.String(24), primary_key=True)
    owner_user_id = db.Column(db.String(80), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    mode = db.Column(db.String(20), nullable=False, index=True)
    employee_number = db.Column(db.String(40), nullable=False, default='')
    site_row_id = db.Column(db.Integer, index=True)
    site_id = db.Column(db.String(20), index=True)
    site_name = db.Column(db.String(200))
    site_manager_id = db.Column(db.String(20), index=True)
    site_manager_name = db.Column(db.String(200))
    view_token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    edit_token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    account_shares = db.Column(db.JSON, nullable=False, default=dict)
    assist = db.Column(db.JSON, nullable=False, default=dict)
    extra_data = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.String(32), nullable=False)
    updated_at = db.Column(db.String(32), nullable=False, index=True)

    months = db.relationship(
        'CloudShiftMonth',
        back_populates='project',
        cascade='all, delete-orphan',
        order_by='CloudShiftMonth.year, CloudShiftMonth.month',
    )
    histories = db.relationship(
        'CloudShiftHistory',
        back_populates='project',
        cascade='all, delete-orphan',
        order_by='CloudShiftHistory.id',
    )


class CloudShiftMonth(db.Model):
    __tablename__ = 'cloudshift_months'
    __table_args__ = (
        db.UniqueConstraint('project_id', 'year', 'month', name='uq_cloudshift_month_project_year_month'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.String(24), db.ForeignKey('cloudshift_projects.id'), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)
    capacity_enabled = db.Column(db.Boolean, nullable=False, default=False)
    required_capacity = db.Column(db.Integer, nullable=False, default=0)
    entries_per_day = db.Column(db.JSON, nullable=False, default=dict)
    draft_entries_per_day = db.Column(db.JSON, nullable=False, default=dict)
    revision = db.Column(db.Integer, nullable=False, default=1)
    revision_snapshots = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.String(32), nullable=False)
    updated_at = db.Column(db.String(32), nullable=False)

    project = db.relationship('CloudShiftProject', back_populates='months')


class CloudShiftHistory(db.Model):
    __tablename__ = 'cloudshift_history'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.String(24), db.ForeignKey('cloudshift_projects.id'), nullable=False, index=True)
    timestamp = db.Column(db.String(32), nullable=False, index=True)
    editor_name = db.Column(db.String(100), nullable=False, default='')
    editor_type = db.Column(db.String(30), nullable=False, default='')
    action = db.Column(db.String(60), nullable=False, default='')
    month_key = db.Column(db.String(7), index=True)
    changes = db.Column(db.JSON, nullable=False, default=list)
    payload = db.Column(db.JSON, nullable=False, default=dict)

    project = db.relationship('CloudShiftProject', back_populates='histories')
