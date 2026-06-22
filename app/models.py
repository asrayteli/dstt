import json

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date, timedelta, timezone
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import deferred

from .security.column_crypto import EncryptedText

db = SQLAlchemy()
JST = timezone(timedelta(hours=9))


def jst_now():
    return datetime.now(JST).replace(tzinfo=None)

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


class DsttLoginLog(db.Model):
    """DSTT successful login history."""

    __tablename__ = 'dstt_login_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    logged_in_at = db.Column(db.DateTime, default=jst_now, nullable=False, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'name': self.name or '',
            'ip_address': self.ip_address or '',
            'user_agent': self.user_agent or '',
            'logged_in_at': self.logged_in_at.isoformat() if self.logged_in_at else None,
        }


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


class UserLoginLog(db.Model):
    """DSTT login audit log."""

    __tablename__ = 'user_login_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    success = db.Column(db.Boolean, nullable=False, default=True, index=True)
    logged_in_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)


class UserActivityLog(db.Model):
    """Per-user tool access log captured for admin reporting."""

    __tablename__ = 'user_activity_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    tool_key = db.Column(db.String(80), nullable=False, index=True)
    tool_label = db.Column(db.String(120), nullable=True)
    endpoint = db.Column(db.String(160), nullable=True)
    method = db.Column(db.String(10), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    status_code = db.Column(db.Integer, nullable=False, index=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FilePostDownloadLog(db.Model):
    """FILE POST download audit log."""

    __tablename__ = 'filepost_download_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    share_uid = db.Column(db.String(32), nullable=False, index=True)
    display_id = db.Column(db.String(32), nullable=True, index=True)
    uploader = db.Column(db.String(80), nullable=True, index=True)
    downloader = db.Column(db.String(80), nullable=True, index=True)
    downloader_label = db.Column(db.String(120), nullable=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_index = db.Column(db.Integer, nullable=True)
    download_type = db.Column(db.String(20), nullable=False, default='file')
    file_size = db.Column(db.BigInteger, nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FilePostUploadLog(db.Model):
    """FILE POST upload audit log."""

    __tablename__ = 'filepost_upload_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    share_uid = db.Column(db.String(32), nullable=False, index=True)
    display_id = db.Column(db.String(32), nullable=True, index=True)
    uploader = db.Column(db.String(80), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_index = db.Column(db.Integer, nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True)
    is_internal = db.Column(db.Boolean, nullable=False, default=False, index=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


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


class PowerVoteForm(db.Model):
    """PowerVote form owned by a DSTT user."""

    __tablename__ = 'powervote_forms'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owner_user_id = db.Column(db.String(80), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False, default='Untitled PowerVote')
    description = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='draft', index=True)
    public_token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    is_anonymous = db.Column(db.Boolean, nullable=False, default=True)
    identity_mode = db.Column(db.String(20), nullable=False, default='anonymous')
    cookie_limit_enabled = db.Column(db.Boolean, nullable=False, default=True)
    result_visibility = db.Column(db.String(20), nullable=False, default='creator_only')
    theme = db.Column(db.JSON, nullable=False, default=dict)
    settings = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    questions = db.relationship(
        'PowerVoteQuestion',
        back_populates='form',
        cascade='all, delete-orphan',
        order_by='PowerVoteQuestion.sort_order',
    )
    responses = db.relationship(
        'PowerVoteResponse',
        back_populates='form',
        cascade='all, delete-orphan',
        order_by='PowerVoteResponse.submitted_at.desc()',
    )


class PowerVoteQuestion(db.Model):
    """Flexible PowerVote question block."""

    __tablename__ = 'powervote_questions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    form_id = db.Column(db.Integer, db.ForeignKey('powervote_forms.id'), nullable=False, index=True)
    question_type = db.Column(db.String(40), nullable=False, default='short_text')
    title = db.Column(db.String(300), nullable=False, default='')
    description = db.Column(db.Text, nullable=False, default='')
    is_required = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    options = db.Column(db.JSON, nullable=False, default=list)
    settings = db.Column(db.JSON, nullable=False, default=dict)

    form = db.relationship('PowerVoteForm', back_populates='questions')


class PowerVoteResponse(db.Model):
    """Submitted answers for a PowerVote form."""

    __tablename__ = 'powervote_responses'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    form_id = db.Column(db.Integer, db.ForeignKey('powervote_forms.id'), nullable=False, index=True)
    respondent_name = db.Column(db.String(200), nullable=False, default='')
    respondent_email = db.Column(db.String(200), nullable=False, default='')
    respondent_cookie_id = db.Column(db.String(128), nullable=False, default='', index=True)
    answers = db.Column(db.JSON, nullable=False, default=dict)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    form = db.relationship('PowerVoteForm', back_populates='responses')


class ToolCategory(db.Model):
    """ツールカテゴリ（管理者が自由に作成・編集・削除可能）"""
    __tablename__ = 'tool_categories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tool_settings = db.relationship(
        'ToolSettings',
        back_populates='category',
        order_by='ToolSettings.sort_order',
    )

    def to_dict(self, include_tools=False):
        data = {
            'id': self.id,
            'name': self.name,
            'sort_order': self.sort_order,
        }
        if include_tools:
            data['tools'] = [ts.to_dict() for ts in self.tool_settings]
        return data


class ToolSettings(db.Model):
    """ツールごとの公開/非公開・カテゴリ紐付け・アクセス種別"""
    __tablename__ = 'tool_settings'

    tool_key = db.Column(db.String(80), primary_key=True)
    is_visible = db.Column(db.Boolean, nullable=False, default=True)
    access_type = db.Column(db.String(20), nullable=False, default='public')
    category_id = db.Column(db.Integer, db.ForeignKey('tool_categories.id'), nullable=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('ToolCategory', back_populates='tool_settings')

    def to_dict(self):
        return {
            'tool_key': self.tool_key,
            'is_visible': self.is_visible,
            'access_type': self.access_type,
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else None,
            'sort_order': self.sort_order,
        }


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
    company_mobile = db.Column(db.String(20))  # 会社携帯（ファイルC）
    email = db.Column(db.String(255))  # メールアドレス（ファイルC）

    # その他
    health_insurance = db.Column(db.String(20))  # 健康保険加入区分

    # システム情報
    is_deleted = db.Column(db.Boolean, default=False, index=True)  # 削除フラグ
    is_retired = db.Column(db.Boolean, default=False, index=True)  # 退職者フラグ（ファイルA更新で名簿から外れた社員）
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
            'company_mobile': self.company_mobile,
            'email': self.email,
            'health_insurance': self.health_insurance,
            'is_deleted': self.is_deleted,
            'is_retired': self.is_retired,
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


class ContactUploadHistory(db.Model):
    """連絡先ファイル（ファイルC: メールアドレス/会社携帯）アップロード履歴"""
    __tablename__ = 'contact_upload_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uploaded_by = db.Column(db.String(80), nullable=False)  # アップロードユーザーID
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    filename = db.Column(db.String(255))  # ファイル名
    success_count = db.Column(db.Integer, default=0)  # 更新件数
    skip_count = db.Column(db.Integer, default=0)  # スキップ件数
    error_count = db.Column(db.Integer, default=0)  # エラー件数
    total_rows = db.Column(db.Integer, default=0)  # 総行数

    def __repr__(self):
        return f'<ContactUploadHistory {self.id}: {self.filename} by {self.uploaded_by}>'


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
    vehicle_number = db.Column(db.String(40), index=True)
    vehicle_number_updated_by = db.Column(db.String(80))
    vehicle_number_updated_at = db.Column(db.DateTime)
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
            'vehicle_number': self.vehicle_number or "",
            'vehicle_number_updated_by': self.vehicle_number_updated_by,
            'vehicle_number_updated_at': self.vehicle_number_updated_at.isoformat() if self.vehicle_number_updated_at else None,
            'is_active': self.is_active,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<SiteContractMaster {self.contract_code}>'


class VehicleInspectionRecord(db.Model):
    __tablename__ = 'vehicle_inspection_records'
    __table_args__ = (
        db.UniqueConstraint('contract_code', 'vehicle_number', name='uq_vehicle_inspection_contract_vehicle'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    contract_code = db.Column(db.String(20), nullable=False, index=True)
    vehicle_number = db.Column(db.String(40), nullable=False, index=True)
    registration_number = db.Column(db.String(80), index=True)
    expiry_date = db.Column(db.String(8), index=True)
    first_registration_month = db.Column(db.String(40))
    passenger_capacity = db.Column(db.String(40))
    displacement = db.Column(db.String(40))
    site_name = db.Column(db.String(200))
    original_filename = db.Column(db.String(255))
    stored_filename = db.Column(db.String(255))
    stored_path = db.Column(db.String(500))
    uploaded_by = db.Column(db.String(80), index=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    source = db.Column(db.String(30), nullable=False, default='ocr')
    notes = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': self.id,
            'contract_code': self.contract_code,
            'vehicle_number': self.vehicle_number,
            'registration_number': self.registration_number or '',
            'expiry_date': self.expiry_date or '',
            'first_registration_month': self.first_registration_month or '',
            'passenger_capacity': self.passenger_capacity or '',
            'displacement': self.displacement or '',
            'site_name': self.site_name or '',
            'original_filename': self.original_filename or '',
            'stored_filename': self.stored_filename or '',
            'has_pdf': bool(self.stored_path),
            'uploaded_by': self.uploaded_by or '',
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'source': self.source,
            'notes': self.notes or '',
        }


class MailMessage(db.Model):
    """DSTT 共通メール送信基盤のメッセージキュー。

    各ツールは ``app.services.mail_service.queue_mail()`` でこのテーブルに
    メールを積み、スケジューラ（または即時送信）が ``dispatch_pending()`` で
    SMTP 実送信する。``dedupe_key`` により同一通知の二重送信を防ぐ。
    車検証ツールに限らず DSTT 全体から再利用できる汎用基盤。
    """
    __tablename__ = 'mail_messages'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category = db.Column(db.String(50), nullable=False, default='general', index=True)
    to_address = db.Column(db.String(255), nullable=False, index=True)
    to_name = db.Column(db.String(200))
    cc = db.Column(db.String(1000))
    bcc = db.Column(db.String(1000))
    reply_to = db.Column(db.String(255))
    subject = db.Column(db.String(500), nullable=False)
    body_text = db.Column(db.Text, nullable=False, default='')
    body_html = db.Column(db.Text)
    # queued / sending / sent / failed / skipped / canceled
    status = db.Column(db.String(20), nullable=False, default='queued', index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=5)
    last_error = db.Column(db.Text)
    dedupe_key = db.Column(db.String(255), unique=True, index=True)
    related_type = db.Column(db.String(50), index=True)
    related_key = db.Column(db.String(120), index=True)
    scheduled_at = db.Column(db.DateTime, index=True)  # None=即時対象
    sent_at = db.Column(db.DateTime)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=jst_now, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=jst_now, onupdate=jst_now)

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category,
            'to_address': self.to_address,
            'to_name': self.to_name or '',
            'subject': self.subject,
            'status': self.status,
            'attempts': self.attempts,
            'last_error': self.last_error or '',
            'dedupe_key': self.dedupe_key or '',
            'related_type': self.related_type or '',
            'related_key': self.related_key or '',
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class InboundMail(db.Model):
    """DSTT 共通メール基盤の受信トレイ（IMAP 取り込み）。

    ``app.services.mail_inbox`` が IMAP サーバーを定期ポーリングし、新着メールを
    パースしてこのテーブルへ保存する。管理者ページの Webメーラーから閲覧する。
    本文は肥大化を避けるため上限付きで保存し、添付はメタ情報のみ保持する。
    """
    __tablename__ = 'inbound_mails'
    __table_args__ = (
        db.UniqueConstraint('mailbox', 'imap_uid', name='uq_inbound_mailbox_uid'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mailbox = db.Column(db.String(120), nullable=False, default='INBOX', index=True)
    imap_uid = db.Column(db.String(40), nullable=False, index=True)
    message_id = db.Column(db.String(500), index=True)
    from_address = db.Column(db.String(255), index=True)
    from_name = db.Column(db.String(255))
    to_address = db.Column(db.String(1000))
    cc = db.Column(db.String(1000))
    subject = db.Column(db.String(500))
    body_text = db.Column(db.Text)
    body_html = db.Column(db.Text)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    has_attachments = db.Column(db.Boolean, nullable=False, default=False)
    attachments = db.Column(db.Text)  # JSON: [{"name","size","content_type"}]
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    sent_at = db.Column(db.DateTime, index=True)       # 送信元ヘッダの Date
    received_at = db.Column(db.DateTime, nullable=False, default=jst_now, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=jst_now)

    def attachment_list(self):
        if not self.attachments:
            return []
        try:
            data = json.loads(self.attachments)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def to_dict(self, *, include_body: bool = False):
        data = {
            'id': self.id,
            'mailbox': self.mailbox,
            'from_address': self.from_address or '',
            'from_name': self.from_name or '',
            'to_address': self.to_address or '',
            'cc': self.cc or '',
            'subject': self.subject or '(件名なし)',
            'size_bytes': self.size_bytes or 0,
            'has_attachments': bool(self.has_attachments),
            'attachments': self.attachment_list(),
            'is_read': bool(self.is_read),
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'received_at': self.received_at.isoformat() if self.received_at else None,
        }
        if include_body:
            data['body_text'] = self.body_text or ''
            data['body_html'] = self.body_html or ''
        return data


class DriverVehicleProfile(db.Model):
    """運転士（自社社員）1人のマイカー管理プロファイル。社員1人＝1台。

    社員名簿PLUS（``employees``）の社員番号と紐づけ、車検証・自賠責保険・
    任意保険・運転免許証の期限と書類PDFを ``DriverDocument`` 子テーブルで管理する。
    """
    __tablename__ = 'driver_vehicle_profiles'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_number = db.Column(db.String(20), nullable=False, unique=True, index=True)
    employee_name = db.Column(db.String(100))   # 社員名スナップショット
    office_code = db.Column(db.String(20), index=True)
    office_name = db.Column(db.String(100))
    # 通知先メール（社員名簿の値を初期値とし、上書き可能）
    email = db.Column(db.String(255))
    notify_enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    # マイカー情報
    car_maker = db.Column(db.String(100))
    car_name = db.Column(db.String(100))
    registration_number = db.Column(db.String(80), index=True)  # 登録番号/ナンバー
    notes = db.Column(db.Text)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=jst_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=jst_now, onupdate=jst_now)

    documents = db.relationship(
        'DriverDocument',
        back_populates='profile',
        cascade='all, delete-orphan',
        order_by='DriverDocument.doc_type',
    )

    def to_dict(self, *, include_documents=True):
        data = {
            'id': self.id,
            'employee_number': self.employee_number,
            'employee_name': self.employee_name or '',
            'office_code': self.office_code or '',
            'office_name': self.office_name or '',
            'email': self.email or '',
            'notify_enabled': bool(self.notify_enabled),
            'car_maker': self.car_maker or '',
            'car_name': self.car_name or '',
            'registration_number': self.registration_number or '',
            'notes': self.notes or '',
            'created_by': self.created_by or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_documents:
            data['documents'] = {doc.doc_type: doc.to_dict() for doc in self.documents}
        return data


class DriverDocument(db.Model):
    """運転士マイカーの証類（車検証 / 自賠責保険証 / 任意保険証 / 運転免許証）。"""
    __tablename__ = 'driver_documents'
    __table_args__ = (
        db.UniqueConstraint('profile_id', 'doc_type', name='uq_driver_document_profile_type'),
    )

    DOC_TYPES = ('inspection', 'liability_insurance', 'voluntary_insurance', 'license')

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    profile_id = db.Column(
        db.Integer,
        db.ForeignKey('driver_vehicle_profiles.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    doc_type = db.Column(db.String(30), nullable=False, index=True)
    expiry_date = db.Column(db.String(8), index=True)   # YYYYMMDD
    issued_date = db.Column(db.String(8))               # YYYYMMDD
    document_number = db.Column(db.String(120))         # 証券番号 / 免許証番号 等
    issuer = db.Column(db.String(200))                  # 保険会社 / 公安委員会 等
    original_filename = db.Column(db.String(255))
    stored_filename = db.Column(db.String(255))
    stored_path = db.Column(db.String(500))
    notes = db.Column(db.Text)
    # 期限通知の二重送信防止用ステージ（送信済みの「残り日数しきい値」）
    last_notified_stage = db.Column(db.Integer)
    last_notified_at = db.Column(db.DateTime)
    uploaded_by = db.Column(db.String(80))
    uploaded_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=jst_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=jst_now, onupdate=jst_now)

    profile = db.relationship('DriverVehicleProfile', back_populates='documents')

    def to_dict(self):
        return {
            'id': self.id,
            'profile_id': self.profile_id,
            'doc_type': self.doc_type,
            'expiry_date': self.expiry_date or '',
            'issued_date': self.issued_date or '',
            'document_number': self.document_number or '',
            'issuer': self.issuer or '',
            'original_filename': self.original_filename or '',
            'stored_filename': self.stored_filename or '',
            'has_pdf': bool(self.stored_path),
            'notes': self.notes or '',
            'last_notified_stage': self.last_notified_stage,
            'last_notified_at': self.last_notified_at.isoformat() if self.last_notified_at else None,
            'uploaded_by': self.uploaded_by or '',
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


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
    # スナップショットは月データの十数倍になり得るため、必要な経路
    # （フルロード・リビジョン操作）だけが undefer で読み込む。
    revision_snapshots = deferred(db.Column(db.JSON, nullable=False, default=dict))
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


class CloudShiftPwaSubscription(db.Model):
    """CloudShift の ViewPWA 共有先が登録する Web Push 購読。

    同じ端末（クライアント生成の device_id）が同じシフト帳に何度通知許可を
    出しても 1 件に保つため、(project_id, device_id) で一意にし、再購読のたびに
    endpoint / 鍵を最新で上書きする。"""

    __tablename__ = 'cloudshift_pwa_subscriptions'
    __table_args__ = (
        db.UniqueConstraint('project_id', 'device_id', name='uq_cloudshift_pwa_device'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.String(24), db.ForeignKey('cloudshift_projects.id'), nullable=False, index=True)
    device_id = db.Column(db.String(64), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    user_agent = db.Column(db.Text, nullable=False, default='')
    device_label = db.Column(db.String(120), nullable=False, default='')
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_subscription_info(self) -> dict:
        return {
            'endpoint': self.endpoint,
            'keys': {
                'p256dh': self.p256dh,
                'auth': self.auth,
            },
        }


class CloudShiftTemplate(db.Model):
    """現場シフト / 個人シフトの「1か月分」を再利用できるテンプレートとして保存する。

    アシストモーダルの「テンプレート」から、別ウィンドウのカレンダーで 1 か月分を
    作成して保存し、任意の対象月へ「日付基準」または「曜日基準」で反映できる。
    プロジェクト（その現場・その人のシフト帳）に紐付き、所有者のみが管理する。
    PwaSubscription と同様に親プロジェクトへ FK を持つがリレーションは張らず、
    プロジェクト削除時は明示的に削除する（読み込み負荷と孤立行の双方を避ける）。"""

    __tablename__ = 'cloudshift_templates'

    id = db.Column(db.String(24), primary_key=True)
    project_id = db.Column(db.String(24), db.ForeignKey('cloudshift_projects.id'), nullable=False, index=True)
    owner_user_id = db.Column(db.String(80), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False, default='')
    mode = db.Column(db.String(20), nullable=False, default='scene')
    # 既定の反映基準: 'date'（日付基準）/ 'weekday'（曜日基準）。反映時に上書き可。
    basis = db.Column(db.String(20), nullable=False, default='date')
    # テンプレートを作成した代表月（カレンダー編集の基準）。
    representative_year = db.Column(db.Integer, nullable=False, default=0)
    representative_month = db.Column(db.Integer, nullable=False, default=0)
    # 日(1..31)をキーにしたエントリ配列。曜日基準では各曜日の初出日からパターンを導出する。
    slots = db.Column(db.JSON, nullable=False, default=dict)
    # 反映時の既定オプション（祝日の扱い・上書き方法・対象日フィルタなど）。
    options = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.String(32), nullable=False)
    updated_at = db.Column(db.String(32), nullable=False, index=True)


class ToBellTask(db.Model):
    """To Bell task. Phase 1 keeps visibility to task participants."""

    __tablename__ = 'to_bell_tasks'

    __table_args__ = (
        # 連携タスクの重複生成（複数 gunicorn ワーカー間の同時取り込み・フックの
        # 二重送信）を DB レベルで防ぐ。source_ref_id が NULL の手動作成タスクは
        # NULL 同士が distinct 扱いのため、この一意制約の影響を受けない。
        db.Index(
            'uq_to_bell_task_source',
            'source_tool', 'source_ref_type', 'source_ref_id',
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(24), nullable=False, default='todo', index=True)
    priority = db.Column(db.String(16), nullable=False, default='normal', index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    start_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(80), nullable=False, index=True)
    assigned_to = db.Column(db.String(80), nullable=True, index=True)
    reviewer_id = db.Column(db.String(80), nullable=True, index=True)
    project_id = db.Column(db.Integer, nullable=True, index=True)
    visibility_scope = db.Column(db.String(32), nullable=False, default='participants')
    visibility_branch_id = db.Column(db.Integer, nullable=True)
    visibility_office_id = db.Column(db.Integer, nullable=True)
    visibility_department_id = db.Column(db.Integer, nullable=True)
    progress_mode = db.Column(db.String(16), nullable=False, default='auto')
    manual_progress = db.Column(db.Integer, nullable=False, default=0)
    source_tool = db.Column(db.String(80), nullable=True, index=True)
    source_ref_type = db.Column(db.String(80), nullable=True)
    source_ref_id = db.Column(db.String(120), nullable=True)
    pinned = db.Column(db.Boolean, nullable=False, default=False, index=True)
    gcal_event_id = db.Column(db.String(255), nullable=True, index=True)
    gcal_synced_by = db.Column(db.String(80), nullable=True, index=True)
    gcal_reminder_override = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    subtasks = db.relationship(
        'ToBellSubtask',
        back_populates='task',
        cascade='all, delete-orphan',
        order_by='ToBellSubtask.sort_order, ToBellSubtask.id',
    )
    comments = db.relationship(
        'ToBellComment',
        back_populates='task',
        cascade='all, delete-orphan',
        order_by='ToBellComment.created_at.asc()',
    )
    notifications = db.relationship(
        'ToBellNotification',
        back_populates='task',
        cascade='all, delete-orphan',
    )
    tags = db.relationship(
        'ToBellTag',
        secondary='to_bell_task_tags',
        back_populates='tasks',
    )
    attachments = db.relationship(
        'ToBellAttachment',
        back_populates='task',
        cascade='all, delete-orphan',
        order_by='ToBellAttachment.created_at.asc()',
    )

    def progress_percent(self) -> int:
        if self.subtasks:
            done = len([item for item in self.subtasks if item.is_done])
            return int(round((done / len(self.subtasks)) * 100))
        return max(0, min(100, int(self.manual_progress or 0)))

    def to_dict(self, include_detail: bool = True) -> dict:
        data = {
            'id': self.id,
            'title': self.title,
            'description': self.description or '',
            'status': self.status,
            'priority': self.priority,
            'due_at': self.due_at.isoformat() if self.due_at else None,
            'start_at': self.start_at.isoformat() if self.start_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_by': self.created_by,
            'assigned_to': self.assigned_to or '',
            'reviewer_id': self.reviewer_id or '',
            'project_id': self.project_id,
            'visibility_scope': self.visibility_scope,
            'progress_mode': self.progress_mode,
            'manual_progress': int(self.manual_progress or 0),
            'progress': self.progress_percent(),
            'source_tool': self.source_tool or '',
            'source_ref_type': self.source_ref_type or '',
            'source_ref_id': self.source_ref_id or '',
            'pinned': bool(self.pinned),
            'gcal_synced': bool(self.gcal_event_id),
            'gcal_reminder_override': self.gcal_reminder_override,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'tags': [tag.to_dict() for tag in self.tags],
        }
        if include_detail:
            data['subtasks'] = [item.to_dict() for item in self.subtasks]
            data['comments'] = [item.to_dict() for item in self.comments]
            data['attachments'] = [item.to_dict() for item in self.attachments]
        return data


class ToBellSubtask(db.Model):
    __tablename__ = 'to_bell_subtasks'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    is_done = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    task = db.relationship('ToBellTask', back_populates='subtasks')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'task_id': self.task_id,
            'title': self.title,
            'is_done': bool(self.is_done),
            'sort_order': int(self.sort_order or 0),
        }


class ToBellComment(db.Model):
    __tablename__ = 'to_bell_comments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    task = db.relationship('ToBellTask', back_populates='comments')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'task_id': self.task_id,
            'body': self.body,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ToBellTag(db.Model):
    __tablename__ = 'to_bell_tags'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(80), nullable=False, index=True)
    color = db.Column(db.String(20), nullable=False, default='#2563eb')
    created_by = db.Column(db.String(80), nullable=False, index=True)

    tasks = db.relationship(
        'ToBellTask',
        secondary='to_bell_task_tags',
        back_populates='tags',
    )

    def to_dict(self) -> dict:
        return {'id': self.id, 'name': self.name, 'color': self.color}


class ToBellTaskTag(db.Model):
    __tablename__ = 'to_bell_task_tags'
    __table_args__ = (
        db.UniqueConstraint('task_id', 'tag_id', name='uq_to_bell_task_tag'),
    )

    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('to_bell_tags.id'), primary_key=True)


class ToBellNotification(db.Model):
    __tablename__ = 'to_bell_notifications'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(80), nullable=False, index=True)
    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), nullable=True, index=True)
    source_tool = db.Column(db.String(80), nullable=True, index=True)
    event_type = db.Column(db.String(80), nullable=False, default='task')
    title = db.Column(db.String(240), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    href = db.Column(db.String(500), nullable=False, default='/tools/to_bell')
    severity = db.Column(db.String(20), nullable=False, default='info')
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)
    is_resolved = db.Column(db.Boolean, nullable=False, default=False, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    task = db.relationship('ToBellTask', back_populates='notifications')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'task_id': self.task_id,
            'source_tool': self.source_tool or '',
            'event_type': self.event_type,
            'title': self.title,
            'body': self.body or '',
            'href': self.href,
            'severity': self.severity,
            'is_read': bool(self.is_read),
            'is_resolved': bool(self.is_resolved),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ToBellPushSubscription(db.Model):
    __tablename__ = 'to_bell_push_subscriptions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(80), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    user_agent = db.Column(db.Text, nullable=False, default='')
    device_label = db.Column(db.String(120), nullable=False, default='')
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_subscription_info(self) -> dict:
        return {
            'endpoint': self.endpoint,
            'keys': {
                'p256dh': self.p256dh,
                'auth': self.auth,
            },
        }


class ToBellPushDelivery(db.Model):
    __tablename__ = 'to_bell_push_deliveries'
    __table_args__ = (
        db.UniqueConstraint('task_id', 'user_id', 'due_at_key', name='uq_to_bell_push_delivery'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), nullable=False, index=True)
    user_id = db.Column(db.String(80), nullable=False, index=True)
    due_at_key = db.Column(db.String(32), nullable=False, index=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default='sent')


class ToBellGoogleAccount(db.Model):
    """ユーザー個人の Google アカウント接続（ToBell → Google Calendar 連携用）。

    refresh_token / access_token は EncryptedText で暗号化して保存する。
    """
    __tablename__ = 'to_bell_google_accounts'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    google_email = db.Column(db.String(255), nullable=False, default='')
    refresh_token = db.Column(EncryptedText, nullable=True)
    access_token = db.Column(EncryptedText, nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    scope = db.Column(db.Text, nullable=False, default='')
    connected_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Google Calendar → ToBell 取り込み用の状態。
    # calendar_sync_token: incremental sync の nextSyncToken（無ければ初回は時間窓で取得）。
    # last_import_at: 最終取り込み時刻（ユーザー選択の取り込み間隔の判定に使う）。
    calendar_sync_token = db.Column(db.Text, nullable=True)
    last_import_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            'connected': bool(self.refresh_token),
            'google_email': self.google_email or '',
            'connected_at': self.connected_at.isoformat() if self.connected_at else None,
        }


class ToBellShareToken(db.Model):
    """ログイン不要で「そのユーザーとして ToBell のみ」操作できる共有トークン。

    トークンは URL から渡され、サーバー側で username に解決される。
    `/tools/to_bell/` 配下の経路でのみ有効（request_loader でパス制限）。"""
    __tablename__ = 'to_bell_share_tokens'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(80), nullable=False, unique=True, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    is_revoked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)

    def is_usable(self) -> bool:
        return not self.is_revoked


class ToBellProject(db.Model):
    """タスクのまとまり。所属内で共有できる。"""
    __tablename__ = 'to_bell_projects'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(24), nullable=False, default='active', index=True)
    color = db.Column(db.String(20), nullable=False, default='#2563eb')
    owner_id = db.Column(db.String(80), nullable=False, index=True)
    visibility_scope = db.Column(db.String(32), nullable=False, default='office')
    office_id = db.Column(db.Integer, nullable=True, index=True)
    calendar_only = db.Column(db.Boolean, nullable=False, default=False)
    pinned = db.Column(db.Boolean, nullable=False, default=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    members = db.relationship(
        'ToBellProjectMember', cascade='all, delete-orphan', backref='project', lazy='selectin'
    )

    def to_dict(self, *, task_count: int | None = None, open_count: int | None = None) -> dict:
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'status': self.status,
            'color': self.color,
            'owner_id': self.owner_id,
            'visibility_scope': self.visibility_scope,
            'calendar_only': bool(self.calendar_only),
            'pinned': bool(self.pinned),
            'sort_order': int(self.sort_order or 0),
            'members': [m.username for m in (self.members or [])],
        }
        if task_count is not None:
            data['task_count'] = task_count
        if open_count is not None:
            data['open_count'] = open_count
        return data


class ToBellProjectMember(db.Model):
    __tablename__ = 'to_bell_project_members'
    __table_args__ = (
        db.UniqueConstraint('project_id', 'username', name='uq_to_bell_project_member'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('to_bell_projects.id'), nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ToBellAttachment(db.Model):
    __tablename__ = 'to_bell_attachments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.Integer, db.ForeignKey('to_bell_tasks.id'), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False, default='')
    file_size = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    task = db.relationship('ToBellTask', back_populates='attachments')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'task_id': self.task_id,
            'file_name': self.file_name,
            'mime_type': self.mime_type or '',
            'file_size': int(self.file_size or 0),
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'href': f'/tools/to_bell/api/attachments/{self.id}',
        }


class ToBellTemplate(db.Model):
    """ユーザーが作成したテンプレートのみ保存する（サーバー側の業務テンプレートは持たない）。"""
    __tablename__ = 'to_bell_templates'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    owner_id = db.Column(db.String(80), nullable=False, index=True)
    scope = db.Column(db.String(32), nullable=False, default='private')
    office_id = db.Column(db.Integer, nullable=True, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        payload = self.payload if isinstance(self.payload, dict) else {}
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'owner_id': self.owner_id,
            'scope': self.scope,
            'is_hidden': bool(self.is_hidden),
            'subtask_count': len(payload.get('subtasks') or []),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ToBellUserSettings(db.Model):
    """ToBell のユーザー個人設定。DSTT連携トグル等を保存する。"""
    __tablename__ = 'to_bell_user_settings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    integrations = db.Column(db.JSON, nullable=False, default=dict)
    preferences = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ToBellEmployeeLink(db.Model):
    """ToBell プロジェクト/タスクと pluslist 社員の多対多紐付け。"""
    __tablename__ = 'to_bell_employee_links'
    __table_args__ = (
        db.UniqueConstraint('target_type', 'target_id', 'employee_id', name='uq_tobell_employee_link'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    target_type = db.Column(db.String(16), nullable=False, index=True)  # 'project' | 'task'
    target_id = db.Column(db.Integer, nullable=False, index=True)
    employee_id = db.Column(db.Integer, nullable=False, index=True)
    created_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ToBellSiteLink(db.Model):
    """ToBell プロジェクト/タスクと siteplus 現場の多対多紐付け。"""
    __tablename__ = 'to_bell_site_links'
    __table_args__ = (
        db.UniqueConstraint('target_type', 'target_id', 'site_row_id', name='uq_tobell_site_link'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    target_type = db.Column(db.String(16), nullable=False, index=True)  # 'project' | 'task'
    target_id = db.Column(db.Integer, nullable=False, index=True)
    site_row_id = db.Column(db.Integer, nullable=False, index=True)
    created_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ToBellExternalFile(db.Model):
    """ToBell のプロジェクト/タスクに紐付ける FILEPOST 非公開ファイル。"""
    __tablename__ = 'to_bell_external_files'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    target_type = db.Column(db.String(16), nullable=False, index=True)  # 'project' | 'task'
    target_id = db.Column(db.Integer, nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default='filepost')
    share_id = db.Column(db.String(80), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    mime_type = db.Column(db.String(120), nullable=True)
    private_url = db.Column(db.String(500), nullable=False)
    download_token = db.Column(db.String(120), nullable=True)
    note = db.Column(db.Text, nullable=False, default='')
    uploaded_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'provider': self.provider,
            'share_id': self.share_id,
            'file_name': self.file_name,
            'file_size': int(self.file_size or 0),
            'mime_type': self.mime_type or '',
            'private_url': self.private_url,
            'note': self.note or '',
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 健診PLUS（health_check）
# ============================================================

class HealthCheckRecord(db.Model):
    """健診レコード本体。対象者 × 健診年度で1レコード（年度履歴を保持）。

    record_type:
      - linked   : 社員名簿PLUS（Employee）に存在する現職社員
      - pre_hire : 入社前健診の対象者（名簿未登録。入社後に linked へ昇格可能）
      - internal : 名簿に載らない内勤・嘱託等
    """

    __tablename__ = 'health_check_records'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    target_year = db.Column(db.Integer, nullable=False, index=True)   # 健診年度（4月開始）
    record_type = db.Column(db.String(20), nullable=False, default='linked', index=True)

    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True, index=True)

    # 名簿からの同期項目（手動モードでは手入力）
    office_code = db.Column(db.String(20), nullable=True, index=True)   # アクセス制御の基準
    employee_number = db.Column(db.String(20), nullable=True, index=True)  # ① 社員番号
    employee_name = db.Column(db.String(100), nullable=False)              # ② 社員名
    employee_type = db.Column(db.String(50), nullable=True)               # ③ 社員区分
    assignment_site = db.Column(db.String(200), nullable=True)            # ④ 専従先名（法人名）
    manager_name = db.Column(db.String(100), nullable=True)               # ⑤ 管理担当名
    manager_user = db.Column(db.String(80), nullable=True, index=True)    # ⑤→DSTTユーザー(username)
    hire_date = db.Column(db.Date, nullable=True)                         # ⑥ 入社日付
    retirement_date = db.Column(db.String(50), nullable=True)            # ⑦ 退職日付
    is_retired = db.Column(db.Boolean, nullable=False, default=False, index=True)  # 退職者フラグ（名簿PLUS同期）
    birth_date = db.Column(db.Date, nullable=True)                       # 生年月日（名簿同期・NASVA判定用）

    # 受診系
    reservation_date = db.Column(db.Date, nullable=True)                 # ⑧ 予約日
    exam_date = db.Column(db.Date, nullable=True)                        # ⑨ 受診日
    exam_date_2 = db.Column(db.Date, nullable=True)                      # ⑩ 受診日②（深夜従事者の年2回目）
    exam_date_2_target = db.Column(db.Date, nullable=True)               # ⑩のリマインド基準日
    medical_institution = db.Column(db.String(200), nullable=True)       # ⑪ 受診医療機関名
    is_night_worker = db.Column(db.Boolean, nullable=False, default=False)  # ⑫ 深夜従事者

    # 再検査・二次検査
    needs_recheck = db.Column(db.Boolean, nullable=False, default=False)    # ⑬ 再検査有無
    recheck_items = db.Column(db.Text, nullable=True)                       # ⑭ 再検査項目
    secondary_recommended_date = db.Column(db.Date, nullable=True)          # ⑮ 二次検査受診推奨日
    secondary_exam_date = db.Column(db.Date, nullable=True)                 # ⑯ 二次検診受診日
    secondary_guide_sent_date = db.Column(db.Date, nullable=True)           # ⑰ 二次検査案内送付日
    secondary_result = db.Column(db.Text, nullable=True)                    # ⑱ 二次検査結果

    # NASVA（年度4/1基準で64歳超の運転者向け適齢診断）
    nasva_reservation_date = db.Column(db.Date, nullable=True)              # NASVA予約日
    nasva_exam_date = db.Column(db.Date, nullable=True)                     # NASVA受診日

    remarks = db.Column(db.Text, nullable=True)                             # ⑲ 備考

    # 区分フラグ
    is_exempt = db.Column(db.Boolean, nullable=False, default=False)        # 受診非対象者（ヒーローの各カウント対象外）
    is_kintone = db.Column(db.Boolean, nullable=False, default=False)       # kintone（レコード単位の任意フラグ）

    # 通知の追加宛先（レコード個別。username のJSON配列）。
    # 既定の宛先（管理担当者＋営業所の健康診断担当）に上乗せして通知する。
    extra_notify_users = db.Column(db.Text, nullable=True)

    # リマインド設定（個別上書き。Noneのときは全体既定を使う）
    reminder_lead_days = db.Column(db.Integer, nullable=True)

    # システム
    secondary_task_id = db.Column(db.Integer, nullable=True)   # 二次検査ToBellタスクID
    night2_task_id = db.Column(db.Integer, nullable=True)      # 深夜2回目ToBellタスクID
    created_by = db.Column(db.String(80), nullable=True)
    updated_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=jst_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=jst_now, onupdate=jst_now, nullable=False)

    attachments = db.relationship(
        'HealthCheckAttachment',
        back_populates='record',
        cascade='all, delete-orphan',
        order_by='HealthCheckAttachment.created_at.asc()',
    )

    __table_args__ = (
        db.UniqueConstraint('target_year', 'employee_id', name='uq_health_check_year_employee'),
    )

    def compute_status(self) -> str:
        """日付・フラグから受診ステータスを算出する。"""
        if self.secondary_exam_date:
            return '二次完了'
        if self.needs_recheck:
            if self.secondary_guide_sent_date:
                return '二次案内済'
            return '再検査対象'
        if self.exam_date:
            return '受診済'
        if self.reservation_date:
            return '予約済'
        return '未予約'

    def age_at_fiscal_start(self) -> int | None:
        """健診年度の基準日（4/1）時点の満年齢。生年月日が無ければ None。"""
        if not self.birth_date:
            return None
        try:
            reference = date(self.target_year, 4, 1)
        except (TypeError, ValueError):
            return None
        return relativedelta(reference, self.birth_date).years

    def is_nasva_target(self) -> bool:
        """年度4/1基準で64歳を超える（=65歳以上）場合に NASVA 対象。"""
        age = self.age_at_fiscal_start()
        return age is not None and age > 64

    def night_second_status(self) -> str | None:
        """深夜従事者の年2回目の受診状況。対象外なら None。"""
        if not self.is_night_worker:
            return None
        if self.exam_date_2:
            return '2回目受診済'
        if self.exam_date_2_target:
            return '2回目予定'
        return '2回目未設定'

    def recheck_items_list(self) -> list[dict]:
        """再検査項目を [{name, value}] のリストで返す。
        旧仕様のプレーンテキストは1項目（name=本文）として後方互換で扱う。"""
        raw = self.recheck_items
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            text = str(raw).strip()
            return [{'name': text, 'value': ''}] if text else []
        if not isinstance(data, list):
            return []
        result: list[dict] = []
        for item in data:
            if isinstance(item, dict):
                name = str(item.get('name', '')).strip()
                value = str(item.get('value', '')).strip()
                if name or value:
                    result.append({'name': name, 'value': value})
            elif isinstance(item, str):
                s = item.strip()
                if s:
                    result.append({'name': s, 'value': ''})
        return result

    def recheck_items_text(self) -> str:
        """再検査項目を1行表記（CSV等の表示用）に整形する。"""
        parts = []
        for item in self.recheck_items_list():
            name, value = item['name'], item['value']
            if name and value:
                parts.append(f'{name}：{value}')
            else:
                parts.append(name or value)
        return ' / '.join(p for p in parts if p)

    def extra_notify_users_list(self) -> list[str]:
        """レコード個別の追加通知先（username）のリスト。"""
        raw = self.extra_notify_users
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        result: list[str] = []
        for item in data:
            u = str(item).strip()
            if u and u not in result:
                result.append(u)
        return result

    def to_dict(self) -> dict:
        def _d(value):
            return value.isoformat() if value else None

        return {
            'id': self.id,
            'target_year': self.target_year,
            'record_type': self.record_type,
            'employee_id': self.employee_id,
            'office_code': self.office_code or '',
            'employee_number': self.employee_number or '',
            'employee_name': self.employee_name or '',
            'employee_type': self.employee_type or '',
            'assignment_site': self.assignment_site or '',
            'manager_name': self.manager_name or '',
            'manager_user': self.manager_user or '',
            'hire_date': _d(self.hire_date),
            'retirement_date': self.retirement_date or '',
            'is_retired': bool(self.is_retired),
            'birth_date': _d(self.birth_date),
            'reservation_date': _d(self.reservation_date),
            'exam_date': _d(self.exam_date),
            'exam_date_2': _d(self.exam_date_2),
            'exam_date_2_target': _d(self.exam_date_2_target),
            'medical_institution': self.medical_institution or '',
            'is_night_worker': bool(self.is_night_worker),
            'needs_recheck': bool(self.needs_recheck),
            'recheck_items': self.recheck_items or '',
            'recheck_items_list': self.recheck_items_list(),
            'secondary_recommended_date': _d(self.secondary_recommended_date),
            'secondary_exam_date': _d(self.secondary_exam_date),
            'secondary_guide_sent_date': _d(self.secondary_guide_sent_date),
            'secondary_result': self.secondary_result or '',
            'nasva_reservation_date': _d(self.nasva_reservation_date),
            'nasva_exam_date': _d(self.nasva_exam_date),
            'remarks': self.remarks or '',
            'is_exempt': bool(self.is_exempt),
            'is_kintone': bool(self.is_kintone),
            'extra_notify_users': self.extra_notify_users_list(),
            'reminder_lead_days': self.reminder_lead_days,
            'status': self.compute_status(),
            'night_second_status': self.night_second_status(),
            'age_at_fiscal_start': self.age_at_fiscal_start(),
            'is_nasva_target': self.is_nasva_target(),
            'attachment_count': len(self.attachments) if self.attachments is not None else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class HealthCheckAttachment(db.Model):
    """健診結果のスキャン添付（pdf/jpg/png）。"""

    __tablename__ = 'health_check_attachments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_id = db.Column(db.Integer, db.ForeignKey('health_check_records.id'), nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False, default='health')  # 'health' | 'nasva'
    title = db.Column(db.String(120), nullable=True)  # 任意のタイトル（例: 健康診断結果 / 二次検査結果）
    stored_path = db.Column(db.String(500), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=True)
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    uploaded_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=jst_now, nullable=False)

    record = db.relationship('HealthCheckRecord', back_populates='attachments')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'record_id': self.record_id,
            'category': self.category or 'health',
            'title': self.title or '',
            'original_name': self.original_name,
            'content_type': self.content_type or '',
            'file_size': int(self.file_size or 0),
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class HealthCheckEditHistory(db.Model):
    """健診レコードの編集履歴。"""

    __tablename__ = 'health_check_edit_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_id = db.Column(db.Integer, nullable=True, index=True)
    target_year = db.Column(db.Integer, nullable=True)
    employee_name = db.Column(db.String(100), nullable=True)
    edited_by = db.Column(db.String(80), nullable=False)
    edited_at = db.Column(db.DateTime, default=jst_now, nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)  # create / update / delete
    field_name = db.Column(db.String(60), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'record_id': self.record_id,
            'target_year': self.target_year,
            'employee_name': self.employee_name or '',
            'edited_by': self.edited_by,
            'edited_at': self.edited_at.isoformat() if self.edited_at else None,
            'action': self.action,
            'field_name': self.field_name or '',
            'old_value': self.old_value or '',
            'new_value': self.new_value or '',
        }
