"""健診PLUS（health_check）

健康診断の進捗を「予約 → 受診 → 再検査 → 二次検査完了」まで追跡する管理ツール。
社員名簿PLUS（Employee）と連携し、対象者×健診年度で1レコードを保持する。

アクセス制御（健康情報＝要配慮個人情報のため厳格に運用）:
  - 営業所スコープは **DSTTのアクセス権と完全に同期**する（健診PLUS独自の付与は廃止）。
    すなわち閲覧・作成・編集できるのは「DSTTで本人に付与された営業所」のみ。
  - DSTT管理者は全営業所を閲覧・編集できる。
  - 健診PLUS管理者は「自分の営業所」の管理者（健康診断担当の設定等）に限る。
    他営業所へはアクセスできない。
通知（ToBell連携）は担当者個人のオプトイン時のみ起票する。
"""
from __future__ import annotations

import os
import json
import csv
import unicodedata
from io import BytesIO, StringIO
from datetime import datetime, date

from flask import Blueprint, render_template, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import selectinload

from app.utils.atomic_io import write_json_atomic
from app.runtime_paths import runtime_path
from app.models import (
    db,
    Employee,
    Office,
    Site,
    User,
    AccessOffice,
    AccessDepartment,
    HealthCheckRecord,
    HealthCheckAttachment,
    HealthCheckEditHistory,
)
from app.access_control import (
    is_admin_user as _is_dstt_admin,
    user_office_codes as _dstt_user_office_codes,
)
from app.services.to_bell_hooks import (
    ensure_health_check_reminders,
    close_health_check_reminders,
    resync_health_check_recipient,
    HEALTH_CHECK_DEFAULT_LEAD_DAYS,
)

health_check_bp = Blueprint("health_check", __name__, url_prefix="/tools/health_check")

ALLOWED_ATTACHMENT_EXT = {"pdf", "jpg", "jpeg", "png"}
# 配信時に使う Content-Type は拡張子から必ずサーバ側で決める。
# アップロード時のクライアント申告値をそのまま返すと、text/html を宣言した
# 「.pdf」を保存して ?inline=1 で開かせる保存型XSSが成立する（CSPは未導入）。
ATTACHMENT_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB
MAX_EDIT_HISTORY_PER_RECORD = 200

# 手動モード（入社前・内勤者）でレコード追加時に選べる社員区分の候補
EMPLOYEE_TYPE_OPTIONS = [
    "営業社員（R契約）",
    "営業社員（P契約）",
    "営業社員（PT契約）",
]
# 管理担当の候補とするDSTTユーザーの「担当」（AccessDepartment）名
# ＝管理者ページで担当が「エリアマネージャー」に設定されているユーザー
AREA_MANAGER_DEPARTMENT = "エリアマネージャー"

# レコードの編集可能フィールド（モード共通の手入力項目）
DATE_FIELDS = {
    "hire_date",
    "birth_date",
    "reservation_date",
    "exam_date",
    "exam_date_2",
    "exam_date_2_target",
    "secondary_recommended_date",
    "secondary_exam_date",
    "secondary_guide_sent_date",
    "nasva_reservation_date",
    "nasva_exam_date",
}
BOOL_FIELDS = {"is_night_worker", "needs_recheck", "is_exempt", "is_kintone", "is_retired",
               "email_opt_out"}
TEXT_FIELDS = {
    "employee_number",
    "employee_name",
    "employee_type",
    "assignment_site",
    "manager_name",
    "retirement_date",
    "medical_institution",
    "secondary_result",
    "remarks",
    "employee_email",
}
# 名簿連携レコードでは Employee から同期するため、手入力では上書きしない項目
SYNCED_FIELDS = {
    "employee_number",
    "employee_name",
    "employee_type",
    "assignment_site",
    "manager_name",
    "retirement_date",
    "is_retired",
    "hire_date",
    "birth_date",
    # メールアドレスは名簿PLUSが正。健診PLUS側での上書きは許さず、
    # 誤りは名簿側を直してもらう（二重管理を避ける）。
    "employee_email",
}

ATTACHMENT_CATEGORIES = {"health", "nasva"}
ATTACHMENT_TITLE_MAX = 120

# 一覧のページング。1営業所あたり数百名になるため既定で区切る。
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

# 一覧で並び替え可能な列。任意の属性名で order_by すると 500 になるため固定する。
SORTABLE_FIELDS = {
    "employee_number",
    "employee_name",
    "exam_date",
    "reservation_date",
    "target_year",
    "manager_name",
    "assignment_site",
    "created_at",
    "updated_at",
}


def _clean_attachment_title(raw) -> str:
    """添付タイトルを整形（空白除去・最大長制限）。空なら空文字。"""
    title = (raw or "").strip()
    if len(title) > ATTACHMENT_TITLE_MAX:
        title = title[:ATTACHMENT_TITLE_MAX]
    return title


RECHECK_ITEM_NAME_MAX = 120
RECHECK_ITEM_VALUE_MAX = 500
MAX_RECHECK_ITEMS = 50
MAX_EXTRA_NOTIFY_USERS = 20


def _serialize_recheck_items(raw) -> str | None:
    """再検査項目の入力（[{name, value}] のリスト または プレーンテキスト）を
    JSON文字列に正規化する。空なら None。"""
    items: list[dict] = []
    if isinstance(raw, list):
        for entry in raw[:MAX_RECHECK_ITEMS]:
            if isinstance(entry, dict):
                name = str(entry.get("name", "")).strip()[:RECHECK_ITEM_NAME_MAX]
                value = str(entry.get("value", "")).strip()[:RECHECK_ITEM_VALUE_MAX]
            else:
                name, value = str(entry).strip()[:RECHECK_ITEM_NAME_MAX], ""
            if name or value:
                items.append({"name": name, "value": value})
    elif raw not in (None, ""):
        # 旧仕様のプレーンテキストは1項目として保持
        text = str(raw).strip()
        if text:
            items.append({"name": text[:RECHECK_ITEM_NAME_MAX], "value": ""})
    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def _serialize_extra_notify_users(raw, office_code: str | None = None) -> str | None:
    """追加通知先（username配列）を、実在ユーザーに限定してJSON文字列へ正規化する。"""
    if not isinstance(raw, list):
        return None
    usernames: list[str] = []
    for entry in raw:
        u = str(entry).strip()
        if u and u not in usernames:
            usernames.append(u)
    if not usernames:
        return None
    usernames = usernames[:MAX_EXTRA_NOTIFY_USERS]
    users = User.query.filter(User.username.in_(usernames)).all()
    code = (office_code or "").strip()
    if code:
        # 「その宛先がこの営業所の健康情報を受け取ってよいか」は宛先本人の
        # アクセス権だけで決まる。操作者が DSTT 管理者かどうかで緩めてはならない
        # （管理者が営業所外のユーザーを宛先に指定でき、氏名＋二次検査対象という
        # 健康情報が権限外へ配信されていた）。
        users = [u for u in users if code in _dstt_user_office_codes(u)]
    valid = {u.username for u in users}
    filtered = [u for u in usernames if u in valid]
    return json.dumps(filtered, ensure_ascii=False) if filtered else None


MAIL_KINDS = ("reservation", "night_second", "secondary_exam")
MAIL_KIND_LABELS = {
    "reservation": "健康診断の予約日",
    "night_second": "深夜従事者の2回目受診日",
    "secondary_exam": "二次検査の受診推奨日",
}
# 本人あてメールの既定は「送らない」。管理者がコントロールパネルで
# 営業所ごとに明示的に有効化したときだけ送る。
MAIL_NOTIFY_DEFAULT = {"enabled": False, "kinds": {k: False for k in MAIL_KINDS}}


def normalize_mail_notify(raw) -> dict:
    """営業所1件分のメール送信設定を正規化する。"""
    enabled = bool((raw or {}).get("enabled")) if isinstance(raw, dict) else False
    kinds_raw = (raw or {}).get("kinds") if isinstance(raw, dict) else None
    kinds_raw = kinds_raw if isinstance(kinds_raw, dict) else {}
    return {"enabled": enabled, "kinds": {k: bool(kinds_raw.get(k, False)) for k in MAIL_KINDS}}


def get_office_mail_notify(office_code: str | None) -> dict:
    """営業所の本人あてメール送信設定。未設定なら既定（全て送らない）。

    ToBellフックからも参照されるためモジュール関数として提供する。
    """
    code = (office_code or "").strip()
    if not code:
        return dict(MAIL_NOTIFY_DEFAULT)
    table = load_settings().get("mail_notify")
    if not isinstance(table, dict):
        return dict(MAIL_NOTIFY_DEFAULT)
    return normalize_mail_notify(table.get(code))


def office_mail_enabled(office_code: str | None, kind: str) -> bool:
    """その営業所・その種別で本人あてメールを送る設定になっているか。"""
    setting = get_office_mail_notify(office_code)
    return bool(setting["enabled"]) and bool(setting["kinds"].get(kind))


def get_office_display_name(office_code: str | None) -> str:
    """メール本文の問い合わせ先に出す営業所名。

    日次スイープや一括送信はレコードごとに呼ぶため、リクエスト内でメモ化する
    （素直に引くと1レコードあたり access_offices と offices を各1本引く）。
    """
    code = (office_code or "").strip()
    if not code:
        return ""
    cache = _request_cache().setdefault("office_names", {})
    if code in cache:
        return cache[code]
    name = code
    for o in _office_options([code]):
        if o["code"] == code:
            name = o["name"]
            break
    cache[code] = name
    return name


def get_office_health_officers(office_code: str | None) -> list[str]:
    """営業所の「健康診断担当」（username）一覧。設定（settings.json）から読む。
    ToBellフックからも参照されるためモジュール関数として提供する。"""
    code = (office_code or "").strip()
    if not code:
        return []
    officers = load_settings().get("health_officers")
    if not isinstance(officers, dict):
        return []
    value = officers.get(code)
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for u in value:
        u = str(u).strip()
        if u and u not in result:
            result.append(u)
    return result


# ============================================================
# データ領域・設定・権限
# ============================================================

def get_data_path() -> str:
    legacy = os.path.join(current_app.root_path, "static", "health_check")
    return str(runtime_path("tools", "health_check", legacy=legacy))


def get_uploads_path() -> str:
    return os.path.join(get_data_path(), "uploads")


def ensure_data_directories() -> None:
    os.makedirs(get_uploads_path(), exist_ok=True)
    permissions_file = os.path.join(get_data_path(), "permissions.json")
    if not os.path.exists(permissions_file):
        write_json_atomic(permissions_file, {"admins": []})
    settings_file = os.path.join(get_data_path(), "settings.json")
    if not os.path.exists(settings_file):
        write_json_atomic(settings_file, {"global_lead_days": HEALTH_CHECK_DEFAULT_LEAD_DAYS})


_permissions_cache: dict[str, tuple[tuple, dict]] = {}


def load_permissions() -> dict:
    """健診PLUSの管理者(admins)のみを保持する。
    旧バージョンの user_offices（独自営業所付与）は廃止（DSTTのアクセス権に同期）。"""
    path = os.path.join(get_data_path(), "permissions.json")
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        _permissions_cache.pop(path, None)
        return {"admins": []}
    cached = _permissions_cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = {"admins": list(data.get("admins", []) or [])}
            _permissions_cache[path] = (stamp, result)
            return result
    except Exception:
        pass
    return {"admins": []}


def save_permissions(permissions: dict) -> None:
    path = os.path.join(get_data_path(), "permissions.json")
    write_json_atomic(path, permissions)
    _permissions_cache.pop(path, None)


# settings.json の読み込みキャッシュ。キーは (パス, mtime, サイズ)。
# 日次スイープや一括操作はレコードごとに通知設定を参照するため、素直に
# 毎回 open すると 800 名で数千回のファイル I/O になる。mtime が変われば
# 自動的に読み直すので、保存直後の反映が遅れることはない。
_settings_cache: dict[str, tuple[tuple, dict]] = {}


def load_settings() -> dict:
    path = os.path.join(get_data_path(), "settings.json")
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        _settings_cache.pop(path, None)
        return {"global_lead_days": HEALTH_CHECK_DEFAULT_LEAD_DAYS}

    cached = _settings_cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _settings_cache[path] = (stamp, data)
            return data
    except Exception:
        pass
    return {"global_lead_days": HEALTH_CHECK_DEFAULT_LEAD_DAYS}


def save_settings(settings: dict) -> None:
    path = os.path.join(get_data_path(), "settings.json")
    write_json_atomic(path, settings)
    _settings_cache.pop(path, None)


def get_global_lead_days() -> int:
    """全体既定の事前通知リードタイム（日数）。ToBellフックからも参照される。"""
    value = load_settings().get("global_lead_days")
    if isinstance(value, int) and value >= 0:
        return value
    return HEALTH_CHECK_DEFAULT_LEAD_DAYS


def is_dstt_admin() -> bool:
    """DSTT全体の管理者。全営業所を閲覧・編集できる。"""
    return bool(_is_dstt_admin())


def is_health_admin(user_id: str) -> bool:
    """健診PLUS管理者か。DSTT管理者、または健診PLUSで管理者指定された人。

    注意: 健診PLUS管理者は『自分の営業所』の管理者であり、これ自体は
    他営業所へのアクセス権を与えない（営業所スコープは DSTT のアクセス権に同期）。
    管理操作（健康診断担当の設定など）の可否は can_admin_office で判定する。
    """
    if _is_dstt_admin():
        return True
    return user_id in load_permissions().get("admins", [])


def _resolve_user(user_id: str):
    """user_id（username）から User を解決する。current_user を優先。"""
    if getattr(current_user, "is_authenticated", False) and getattr(current_user, "username", None) == user_id:
        return current_user
    return User.query.filter_by(username=user_id).first()


def get_user_offices(user_id: str) -> list[str]:
    """アクセス可能な営業所コード一覧。

    DSTT管理者は全営業所。それ以外は **DSTTで本人に付与された営業所のみ**
    （健診PLUS独自の付与は廃止し、DSTTのアクセス権と同期）。
    """
    if _is_dstt_admin():
        return [o["code"] for o in _office_options()]
    user = _resolve_user(user_id)
    if user is None:
        return []
    try:
        return sorted({c for c in _dstt_user_office_codes(user) if c})
    except Exception:
        return []


def _office_options(codes: list[str] | set[str] | None = None) -> list[dict[str, str]]:
    """Return office choices from DSTT access offices, with PlusList names as fallback/override."""
    wanted = {str(c).strip() for c in (codes or []) if str(c).strip()} if codes is not None else None
    by_code: dict[str, dict[str, str]] = {}

    access_query = AccessOffice.query.filter(AccessOffice.code.isnot(None))
    if wanted is not None:
        if not wanted:
            return []
        access_query = access_query.filter(AccessOffice.code.in_(wanted))
    for office in access_query.order_by(AccessOffice.code, AccessOffice.name).all():
        code = (office.code or "").strip()
        if not code:
            continue
        by_code[code] = {"code": code, "name": office.name or code}

    plus_query = Office.query
    if wanted is not None:
        plus_query = plus_query.filter(Office.office_code.in_(wanted))
    for office in plus_query.order_by(Office.office_code).all():
        code = (office.office_code or "").strip()
        if not code:
            continue
        by_code[code] = {"code": code, "name": office.office_name or by_code.get(code, {}).get("name") or code}

    return [by_code[code] for code in sorted(by_code)]


def has_office_access(user_id: str, office_code: str | None) -> bool:
    """その営業所のレコードを閲覧・作成・編集できるか。"""
    if _is_dstt_admin():
        return True
    code = (office_code or "").strip()
    if not code:
        return False
    return code in get_user_offices(user_id)


def can_admin_office(user_id: str, office_code: str | None) -> bool:
    """その営業所で健診PLUSの管理操作（健康診断担当の設定）ができるか。

    DSTT管理者は全営業所。健診PLUS管理者は『自分の営業所』のみ。
    """
    if _is_dstt_admin():
        return True
    if user_id not in load_permissions().get("admins", []):
        return False
    return has_office_access(user_id, office_code)


# ============================================================
# 補助ロジック
# ============================================================

def current_fiscal_year(today: date | None = None) -> int:
    """健診年度（4月開始）。4〜12月はその年、1〜3月は前年。"""
    today = today or date.today()
    return today.year if today.month >= 4 else today.year - 1


def parse_date_value(value):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_name(value: str | None) -> str:
    """担当者名の表記ゆれを吸収する（NFKC・空白除去・小文字化）。"""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(text.split())  # 全/半角空白をすべて除去
    return text.lower()


def _build_user_name_index() -> dict[str, list[str]]:
    """正規化した表示名 → username 群 のインデックス。"""
    index: dict[str, list[str]] = {}
    for user in User.query.all():
        key = _normalize_name(user.name)
        if not key:
            continue
        index.setdefault(key, []).append(user.username)
    return index


def resolve_manager_user(manager_name: str | None, index: dict[str, list[str]] | None = None) -> str | None:
    """管理担当名を DSTTユーザー(username)に一意解決する。曖昧/不一致は None。"""
    key = _normalize_name(manager_name)
    if not key:
        return None
    if index is None:
        # 全ユーザーを走査するのでリクエスト内で使い回す。レコードごとに
        # 作り直すと一覧表示のたびに件数分の全件SELECTが走る。
        cache = _request_cache()
        index = cache.get("name_index")
        if index is None:
            index = _build_user_name_index()
            cache["name_index"] = index
    matches = index.get(key, [])
    return matches[0] if len(matches) == 1 else None


def sync_from_employee(record: HealthCheckRecord, employee: Employee, *, resolve_manager: bool = True) -> None:
    """名簿（Employee）の値をレコードへ同期する。専従先名は法人名(company_name)。"""
    record.office_code = employee.office_code
    record.employee_number = employee.employee_number
    record.employee_name = employee.employee_name
    record.employee_type = employee.employee_type
    record.assignment_site = employee.company_name
    record.manager_name = employee.manager_name
    record.hire_date = employee.hire_date
    record.retirement_date = employee.retirement_date
    record.is_retired = bool(employee.is_retired)
    record.birth_date = employee.birth_date
    # 本人あてメールの宛先。名簿（ファイルC）のメールアドレスに追従する。
    record.employee_email = (employee.email or "").strip() or None
    # 担当者は未解決のときのみ自動解決（手動上書きを尊重）
    if resolve_manager and not record.manager_user and employee.manager_name:
        manager_user = resolve_manager_user(employee.manager_name)
        record.manager_user = manager_user if _manager_user_allowed(manager_user, employee.office_code) else None


def _linked_employee(record: HealthCheckRecord) -> Employee | None:
    if record.record_type == "linked" and record.employee_id:
        return db.session.get(Employee, record.employee_id)
    return None


def _record_access_office(record: HealthCheckRecord) -> str | None:
    employee = _linked_employee(record)
    if employee:
        return employee.office_code
    return record.office_code


def _sync_linked_record(record: HealthCheckRecord) -> bool:
    employee = _linked_employee(record)
    if not employee:
        return False
    before = (
        record.office_code,
        record.employee_number,
        record.employee_name,
        record.employee_type,
        record.assignment_site,
        record.manager_name,
        record.hire_date,
        record.retirement_date,
        bool(record.is_retired),
        record.birth_date,
        record.manager_user,
        record.extra_notify_users,
        record.employee_email,
    )
    sync_from_employee(record, employee)
    if not _manager_user_allowed(record.manager_user, record.office_code):
        record.manager_user = None
    if record.extra_notify_users:
        try:
            notify_users = json.loads(record.extra_notify_users)
        except Exception:
            notify_users = []
        record.extra_notify_users = _serialize_extra_notify_users(notify_users, record.office_code)
    after = (
        record.office_code,
        record.employee_number,
        record.employee_name,
        record.employee_type,
        record.assignment_site,
        record.manager_name,
        record.hire_date,
        record.retirement_date,
        bool(record.is_retired),
        record.birth_date,
        record.manager_user,
        record.extra_notify_users,
        record.employee_email,
    )
    return before != after


def _request_cache() -> dict:
    """リクエスト内だけ有効なメモ化領域。

    一覧表示は数百レコードを同期するため、レコードごとに User と
    アクセス権営業所を引き直すと N+1 になる（800名で約1,600本のSQL）。
    リクエスト境界を越えないので、権限変更の反映が遅れることはない。
    """
    try:
        from flask import g
        cache = getattr(g, "_hc_cache", None)
        if cache is None:
            cache = {}
            g._hc_cache = cache
        return cache
    except Exception:
        # リクエスト文脈の外（バッチ等）ではキャッシュせず素通しする
        return {}


def _user_office_codes_cached(username: str) -> set[str] | None:
    """username → アクセス可能な営業所コード集合。ユーザーが居なければ None。"""
    cache = _request_cache().setdefault("user_offices", {})
    if username in cache:
        return cache[username]
    user = User.query.filter_by(username=username).first()
    if user is None:
        result = None
    else:
        try:
            result = set(_dstt_user_office_codes(user))
        except Exception:
            result = {user.user_office.code} if user.user_office and user.user_office.code else set()
    cache[username] = result
    return result


def _manager_user_allowed(username: str, office_code: str | None) -> bool:
    username = (username or "").strip()
    if not username:
        return True
    codes = _user_office_codes_cached(username)
    if codes is None:
        return False
    code = (office_code or "").strip()
    if not code:
        return True
    # 判定材料は「宛先本人がその営業所にアクセスできるか」のみ。操作者の
    # 管理者権限で緩めない（_serialize_extra_notify_users と同じ理由）。
    return code in codes


def _check_record_access(user_id: str, record: HealthCheckRecord | None, *, sync: bool = True) -> bool:
    if not record:
        return False
    if not has_office_access(user_id, _record_access_office(record)):
        return False
    if sync and _sync_linked_record(record):
        db.session.commit()
    return True


def _prefetch_employees(records: list[HealthCheckRecord]) -> list[Employee]:
    """名簿連携レコードの Employee をまとめて読み込み、identity map に載せる。

    以降の db.session.get(Employee, id) は identity map に当たるため
    追加のSQLを発行しない（レコードごとの1本ずつを1本にまとめる）。

    **戻り値は呼び出し側で保持すること。** SQLAlchemy の identity map は
    弱参照なので、読み込んだ Employee をどこからも参照しないと GC され、
    結局レコードごとに SELECT が飛ぶ。
    """
    ids = {r.employee_id for r in records if r.record_type == "linked" and r.employee_id}
    if not ids:
        return []
    id_list = list(ids)
    loaded: list[Employee] = []
    # SQLite のバインド変数上限（既定999）に掛からないよう分割する
    for i in range(0, len(id_list), 500):
        loaded.extend(Employee.query.filter(Employee.id.in_(id_list[i:i + 500])).all())
    return loaded


def _sync_record_list(records: list[HealthCheckRecord]) -> None:
    # _keep_alive は使わないが、GC を防ぐために同期が終わるまで保持する
    _keep_alive = _prefetch_employees(records)  # noqa: F841
    changed = False
    for record in records:
        if _sync_linked_record(record):
            changed = True
    if changed:
        db.session.commit()


def record_history(record: HealthCheckRecord | None, user_id: str, action: str,
                   field_name: str | None = None, old_value=None, new_value=None,
                   *, year=None, name=None) -> None:
    history = HealthCheckEditHistory(
        record_id=record.id if record else None,
        target_year=(record.target_year if record else year),
        employee_name=(record.employee_name if record else name),
        edited_by=user_id,
        action=action,
        field_name=field_name,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
    )
    db.session.add(history)
    # 剪定は「そのレコードの履歴」単位で行う。以前はテーブル全体で200件に
    # 制限していたため、数十人分の編集を挟んだだけで最初の対象者の監査証跡が
    # 消えていた（要配慮個人情報の追跡として機能していなかった）。
    # レコードに紐付かない一括操作の履歴（record_id=None）は剪定しない。
    if record is None or record.id is None:
        return
    count = HealthCheckEditHistory.query.filter_by(record_id=record.id).count()
    if count > MAX_EDIT_HISTORY_PER_RECORD:
        old = (HealthCheckEditHistory.query
               .filter_by(record_id=record.id)
               .order_by(HealthCheckEditHistory.edited_at.asc())
               .limit(count - MAX_EDIT_HISTORY_PER_RECORD).all())
        for h in old:
            db.session.delete(h)


def _allowed_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXT


def _safe_original_name(filename: str, ext: str) -> str:
    """表示・ダウンロード用の元ファイル名を作る。

    `secure_filename()` は非ASCIIを全て捨てるため、日本語名のスキャン
    （例「健康診断結果.pdf」）が `pdf` という拡張子なしの名前になっていた。
    実体は uuid 名で保存するので、ここではパス区切り・制御文字だけを落として
    日本語をそのまま残す。
    """
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '/\\:*?"<>|')
    name = name.strip(". ") or f"file.{ext}"
    if not name.lower().endswith(f".{ext}"):
        name = f"{name}.{ext}"
    return name[:255]


def _attachment_download_name(attachment, ext: str) -> str:
    """ダウンロード時のファイル名。拡張子が失われた既存データも救済する。"""
    name = (attachment.original_name or "").strip()
    if not name or name.lower() == ext or "." not in name:
        base = (attachment.title or "").strip() or "健診資料"
        return _safe_original_name(base, ext or "dat")
    return name


# ============================================================
# 画面
# ============================================================

@health_check_bp.route("/")
@login_required
def index():
    ensure_data_directories()
    user_id = str(current_user.username)
    user_offices = set(get_user_offices(user_id))
    accessible_offices = _office_options(None if is_dstt_admin() else user_offices)
    return render_template(
        "health_check.html",
        user_id=user_id,
        user_name=getattr(current_user, "name", "") or user_id,
        is_admin=is_health_admin(user_id),
        is_dstt_admin=is_dstt_admin(),
        offices=accessible_offices,
        current_year=current_fiscal_year(),
        global_lead_days=get_global_lead_days(),
        employee_type_options=EMPLOYEE_TYPE_OPTIONS,
        export_columns=_EXPORT_COLUMNS,
    )


# ============================================================
# レコード API
# ============================================================

def _scoped_query(user_id: str):
    """一覧・ダッシュボード・エクスポートで共有する絞り込み済みクエリを返す。
    戻り値は (query, error_response)。error_response が None でなければそれを返す。
    年度・営業所・区分・各フラグ・フリーワードを request.args から適用する。"""
    user_offices = get_user_offices(user_id)
    if not user_offices and not is_dstt_admin():
        return None, (jsonify({"error": "アクセス権限がありません"}), 403)

    query = HealthCheckRecord.query.outerjoin(Employee, HealthCheckRecord.employee_id == Employee.id)
    if not is_dstt_admin():
        query = query.filter(db.or_(
            db.and_(
                HealthCheckRecord.record_type == "linked",
                HealthCheckRecord.employee_id.isnot(None),
                Employee.id.isnot(None),
                Employee.office_code.in_(user_offices),
            ),
            db.and_(
                db.or_(
                    HealthCheckRecord.record_type != "linked",
                    HealthCheckRecord.employee_id.is_(None),
                    Employee.id.is_(None),
                ),
                HealthCheckRecord.office_code.in_(user_offices),
            ),
        ))

    year = request.args.get("year", type=int)
    if year:
        query = query.filter(HealthCheckRecord.target_year == year)

    office = request.args.get("office", "").strip()
    if office:
        if not has_office_access(user_id, office):
            return None, (jsonify({"error": "この営業所への権限がありません"}), 403)
        query = query.filter(db.or_(
            db.and_(
                HealthCheckRecord.record_type == "linked",
                HealthCheckRecord.employee_id.isnot(None),
                Employee.id.isnot(None),
                Employee.office_code == office,
            ),
            db.and_(
                db.or_(
                    HealthCheckRecord.record_type != "linked",
                    HealthCheckRecord.employee_id.is_(None),
                    Employee.id.is_(None),
                ),
                HealthCheckRecord.office_code == office,
            ),
        ))

    record_type = request.args.get("record_type", "").strip()
    if record_type in ("linked", "pre_hire", "internal"):
        query = query.filter(HealthCheckRecord.record_type == record_type)

    # 退職者は既定で非表示（オプションで表示）
    if request.args.get("include_retired") != "true":
        query = query.filter(HealthCheckRecord.is_retired.is_(False))

    if request.args.get("night_only") == "true":
        query = query.filter(HealthCheckRecord.is_night_worker.is_(True))
    if request.args.get("recheck_only") == "true":
        query = query.filter(HealthCheckRecord.needs_recheck.is_(True))
    if request.args.get("unassigned_only") == "true":
        query = query.filter(
            db.or_(HealthCheckRecord.manager_user.is_(None), HealthCheckRecord.manager_user == "")
        )

    search = request.args.get("search", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            HealthCheckRecord.employee_name.like(like),
            HealthCheckRecord.employee_number.like(like),
            HealthCheckRecord.assignment_site.like(like),
            HealthCheckRecord.manager_name.like(like),
            HealthCheckRecord.medical_institution.like(like),
        ))

    return query, None


@health_check_bp.route("/api/records")
@login_required
def api_records():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error

    sort_by = request.args.get("sort_by", "employee_number")
    sort_order = request.args.get("sort_order", "asc")
    if sort_by not in SORTABLE_FIELDS:
        sort_by = "employee_number"
    col = getattr(HealthCheckRecord, sort_by)
    query = query.order_by(col.desc() if sort_order == "desc" else col.asc())

    records = query.options(selectinload(HealthCheckRecord.attachments)).all()
    _sync_record_list(records)
    # ステータス絞り込み（算出値のため取得後にフィルタ）
    status_filter = request.args.get("status", "").strip()
    items = [r.to_dict() for r in records]
    if status_filter:
        items = [r for r in items if r["status"] == status_filter]

    # ページネーション。ステータスが算出値のため、絞り込み後の配列を切り出す。
    # per_page=0 は「全件」（CSV突合など、意図して全部見たいとき用）。
    total = len(items)
    try:
        per_page = int(request.args.get("per_page", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        per_page = DEFAULT_PAGE_SIZE
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    if per_page <= 0:
        return jsonify({"records": items, "count": total, "total": total,
                        "page": 1, "per_page": 0, "pages": 1})

    per_page = min(per_page, MAX_PAGE_SIZE)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    window = items[start:start + per_page]

    return jsonify({
        # count は後方互換のため「このページの件数」ではなく従来どおり返す値を維持しない。
        # 画面は total を使って「n 件中 x〜y 件」を表示する。
        "records": window,
        "count": len(window),
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    })


@health_check_bp.route("/api/record/<int:record_id>")
@login_required
def api_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403
    data = record.to_dict()
    data["attachments"] = [a.to_dict() for a in record.attachments]
    return jsonify(data)


def _apply_payload(record: HealthCheckRecord, payload: dict, user_id: str) -> None:
    """編集可能フィールドを payload から反映し、履歴を記録する。
    名簿連携レコードでは同期項目（氏名・専従先など）は手入力で上書きしない。"""
    skip = SYNCED_FIELDS if record.record_type == "linked" else set()
    for field in TEXT_FIELDS:
        if field in skip or field not in payload:
            continue
        new_value = (str(payload.get(field)).strip() if payload.get(field) is not None else "") or None
        # 氏名は必須のため空での上書きはしない
        if field == "employee_name" and not new_value:
            continue
        old_value = getattr(record, field)
        if (old_value or None) != (new_value or None):
            record_history(record, user_id, "update", field, old_value, new_value)
            setattr(record, field, new_value)
    for field in DATE_FIELDS:
        if field in skip or field not in payload:
            continue
        new_value = parse_date_value(payload.get(field))
        old_value = getattr(record, field)
        if old_value != new_value:
            record_history(record, user_id, "update", field, old_value, new_value)
            setattr(record, field, new_value)
    for field in BOOL_FIELDS:
        if field in skip or field not in payload:
            continue
        new_value = bool(payload.get(field))
        old_value = bool(getattr(record, field))
        if old_value != new_value:
            record_history(record, user_id, "update", field, old_value, new_value)
            setattr(record, field, new_value)
    # 再検査項目（[{name, value}] のJSON。旧プレーンテキストとも互換）
    if "recheck_items" in payload and "recheck_items" not in skip:
        new_value = _serialize_recheck_items(payload.get("recheck_items"))
        old_value = record.recheck_items
        if (old_value or None) != (new_value or None):
            record_history(record, user_id, "update", "recheck_items", old_value, new_value)
            record.recheck_items = new_value
    # 通知の追加宛先（レコード個別。usernameのJSON配列）
    if "extra_notify_users" in payload and "extra_notify_users" not in skip:
        new_value = _serialize_extra_notify_users(payload.get("extra_notify_users"), record.office_code)
        old_value = record.extra_notify_users
        if (old_value or None) != (new_value or None):
            record_history(record, user_id, "update", "extra_notify_users", old_value, new_value)
            record.extra_notify_users = new_value
    if "reminder_lead_days" in payload:
        raw = payload.get("reminder_lead_days")
        new_value = None
        if raw not in (None, ""):
            try:
                new_value = max(0, int(raw))
            except (TypeError, ValueError):
                new_value = None
        if record.reminder_lead_days != new_value:
            record_history(record, user_id, "update", "reminder_lead_days", record.reminder_lead_days, new_value)
            record.reminder_lead_days = new_value


@health_check_bp.route("/api/record", methods=["POST"])
@login_required
def api_create_record():
    user_id = str(current_user.username)
    payload = request.json or {}

    target_year = payload.get("target_year")
    try:
        target_year = int(target_year)
    except (TypeError, ValueError):
        return jsonify({"error": "対象年度が不正です"}), 400

    record_type = payload.get("record_type", "linked")
    if record_type not in ("linked", "pre_hire", "internal"):
        return jsonify({"error": "区分が不正です"}), 400

    record = HealthCheckRecord(target_year=target_year, record_type=record_type, created_by=user_id, updated_by=user_id)

    if record_type == "linked":
        employee = None
        if payload.get("employee_id"):
            employee = db.session.get(Employee, int(payload["employee_id"]))
        elif payload.get("employee_number"):
            employee = Employee.query.filter_by(
                employee_number=str(payload["employee_number"]).strip(), is_deleted=False
            ).first()
        if not employee:
            return jsonify({"error": "対象の社員が名簿に見つかりません"}), 404
        if not has_office_access(user_id, employee.office_code):
            return jsonify({"error": "この社員の営業所への権限がありません"}), 403
        existing = HealthCheckRecord.query.filter_by(
            target_year=target_year, employee_id=employee.id
        ).first()
        if existing:
            return jsonify({"error": "この社員の当年度レコードは既に存在します", "record_id": existing.id}), 409
        record.employee_id = employee.id
        sync_from_employee(record, employee)
    else:
        name = str(payload.get("employee_name", "")).strip()
        if not name:
            return jsonify({"error": "社員名は必須です"}), 400
        office_code = str(payload.get("office_code", "")).strip()
        if not has_office_access(user_id, office_code):
            return jsonify({"error": "この営業所への権限がありません"}), 403
        record.office_code = office_code
        record.employee_name = name
        manager_user = resolve_manager_user(payload.get("manager_name"))
        record.manager_user = manager_user if _manager_user_allowed(manager_user, office_code) else None

    _apply_payload(record, payload, user_id)
    db.session.add(record)
    db.session.flush()
    record_history(record, user_id, "create", "all", None, "新規作成")
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>", methods=["PUT"])
@login_required
def api_update_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403

    payload = request.json or {}

    _apply_payload(record, payload, user_id)
    record.updated_by = user_id
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>", methods=["DELETE"])
@login_required
def api_delete_record(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403

    close_health_check_reminders(record.id, commit=False)
    # 添付ファイルの実体も削除
    for attachment in record.attachments:
        try:
            os.remove(os.path.join(get_uploads_path(), attachment.stored_path))
        except OSError:
            pass

    # このレコードの項目別履歴（健診項目の旧値・新値＝要配慮個人情報）を消す。
    # record_id は FK ではないため放置すると孤児として残り、さらに SQLite が
    # レコードIDを再利用すると、次に同じIDで作られた別営業所のレコードの
    # 履歴として閲覧できてしまう。
    HealthCheckEditHistory.query.filter_by(record_id=record.id).delete(synchronize_session=False)
    # 削除の事実だけは監査用に残す。ID再利用で他人の履歴として見えないよう
    # record_id は持たせない（年度・氏名・実行者で追跡できる）。
    record_history(None, user_id, "delete", "all", record.employee_name, None,
                   year=record.target_year, name=record.employee_name)
    db.session.delete(record)
    db.session.commit()
    return jsonify({"success": True})


@health_check_bp.route("/api/record/<int:record_id>/manager", methods=["PUT"])
@login_required
def api_set_manager(record_id):
    """担当者(manager_user)の手動上書き。"""
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403

    payload = request.json or {}
    username = (payload.get("manager_user") or "").strip()
    if username:
        if not _manager_user_allowed(username, record.office_code):
            return jsonify({"error": "指定のユーザーが存在しないか、この営業所に割り当てできません"}), 400
    old = record.manager_user
    record.manager_user = username or None
    record_history(record, user_id, "update", "manager_user", old, record.manager_user)
    ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
    db.session.commit()
    return jsonify({"success": True, "record": record.to_dict()})


# ============================================================
# 一括操作
# ============================================================

@health_check_bp.route("/api/bulk_create", methods=["POST"])
@login_required
def api_bulk_create():
    """名簿から対象年度の在籍者全員を一括起票する。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    try:
        target_year = int(payload.get("target_year"))
    except (TypeError, ValueError):
        return jsonify({"error": "対象年度が不正です"}), 400

    requested_offices = payload.get("offices") or []
    accessible = set(get_user_offices(user_id))
    if requested_offices:
        offices = [c for c in requested_offices if c in accessible]
    else:
        offices = sorted(accessible)
    if not offices:
        return jsonify({"error": "対象営業所への権限がありません"}), 403

    # 退職者（retirement_date あり）は一括起票の既定対象から外す
    employees = Employee.query.filter(
        Employee.office_code.in_(offices),
        Employee.is_deleted.is_(False),
        db.or_(Employee.retirement_date.is_(None), Employee.retirement_date == ""),
    ).all()

    existing_ids = {
        r.employee_id
        for r in HealthCheckRecord.query.filter(
            HealthCheckRecord.target_year == target_year,
            HealthCheckRecord.employee_id.isnot(None),
        ).all()
    }

    name_index = _build_user_name_index()
    created = 0
    for employee in employees:
        if employee.id in existing_ids:
            continue
        record = HealthCheckRecord(
            target_year=target_year,
            record_type="linked",
            employee_id=employee.id,
            created_by=user_id,
            updated_by=user_id,
        )
        sync_from_employee(record, employee, resolve_manager=False)
        if employee.manager_name:
            username = resolve_manager_user(employee.manager_name, name_index)
            record.manager_user = username if _manager_user_allowed(username, record.office_code) else None
        db.session.add(record)
        created += 1

    record_history(None, user_id, "bulk_create", "target_year", None, f"{target_year}年度 {created}件", year=target_year)
    db.session.commit()
    return jsonify({"success": True, "created": created, "offices": offices})


@health_check_bp.route("/api/carryover", methods=["POST"])
@login_required
def api_carryover():
    """前年度レコードを新年度へ複製（固定情報のみ。受診系日付はクリア）。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    try:
        from_year = int(payload.get("from_year"))
        to_year = int(payload.get("to_year"))
    except (TypeError, ValueError):
        return jsonify({"error": "年度の指定が不正です"}), 400
    if from_year == to_year:
        return jsonify({"error": "複製元と複製先の年度が同じです"}), 400

    accessible = set(get_user_offices(user_id))
    query = HealthCheckRecord.query.outerjoin(Employee, HealthCheckRecord.employee_id == Employee.id).filter(
        HealthCheckRecord.target_year == from_year,
        HealthCheckRecord.record_type == "linked",
        HealthCheckRecord.employee_id.isnot(None),
    )
    if not is_dstt_admin():
        query = query.filter(db.or_(
            db.and_(Employee.id.isnot(None), Employee.office_code.in_(accessible or ["__none__"])),
            db.and_(Employee.id.is_(None), HealthCheckRecord.office_code.in_(accessible or ["__none__"])),
        ))

    existing_ids = {
        r.employee_id
        for r in HealthCheckRecord.query.filter(
            HealthCheckRecord.target_year == to_year,
            HealthCheckRecord.employee_id.isnot(None),
        ).all()
    }

    created = 0
    for source in query.all():
        if source.employee_id in existing_ids:
            continue
        record = HealthCheckRecord(
            target_year=to_year,
            record_type="linked",
            employee_id=source.employee_id,
            office_code=source.office_code,
            employee_number=source.employee_number,
            employee_name=source.employee_name,
            employee_type=source.employee_type,
            assignment_site=source.assignment_site,
            manager_name=source.manager_name,
            manager_user=source.manager_user,
            hire_date=source.hire_date,
            retirement_date=source.retirement_date,
            is_retired=source.is_retired,
            is_night_worker=source.is_night_worker,
            is_exempt=source.is_exempt,
            is_kintone=source.is_kintone,
            extra_notify_users=source.extra_notify_users,
            reminder_lead_days=source.reminder_lead_days,
            created_by=user_id,
            updated_by=user_id,
        )
        # 名簿連携は最新へ同期（改姓・退職等に追従）
        if record.employee_id:
            employee = db.session.get(Employee, record.employee_id)
            if employee:
                sync_from_employee(record, employee, resolve_manager=False)
        if not _manager_user_allowed(record.manager_user, record.office_code):
            record.manager_user = None
        if record.extra_notify_users:
            try:
                notify_users = json.loads(record.extra_notify_users)
            except Exception:
                notify_users = []
            record.extra_notify_users = _serialize_extra_notify_users(notify_users, record.office_code)
        db.session.add(record)
        created += 1

    record_history(None, user_id, "carryover", "target_year", str(from_year), f"{to_year}年度 {created}件", year=to_year)
    db.session.commit()
    return jsonify({"success": True, "created": created})


@health_check_bp.route("/api/resolve_managers", methods=["POST"])
@login_required
def api_resolve_managers():
    """担当者未解決のレコードを一括で自動紐付けする。"""
    user_id = str(current_user.username)
    payload = request.json or {}
    year = payload.get("year")

    accessible = set(get_user_offices(user_id))
    query = HealthCheckRecord.query.outerjoin(Employee, HealthCheckRecord.employee_id == Employee.id).filter(
        db.or_(HealthCheckRecord.manager_user.is_(None), HealthCheckRecord.manager_user == "")
    )
    if year:
        query = query.filter(HealthCheckRecord.target_year == int(year))
    if not is_dstt_admin():
        query = query.filter(db.or_(
            db.and_(
                HealthCheckRecord.record_type == "linked",
                HealthCheckRecord.employee_id.isnot(None),
                Employee.id.isnot(None),
                Employee.office_code.in_(accessible or ["__none__"]),
            ),
            db.and_(
                db.or_(
                    HealthCheckRecord.record_type != "linked",
                    HealthCheckRecord.employee_id.is_(None),
                    Employee.id.is_(None),
                ),
                HealthCheckRecord.office_code.in_(accessible or ["__none__"]),
            ),
        ))

    name_index = _build_user_name_index()
    resolved = 0
    for record in query.all():
        _sync_linked_record(record)
        username = resolve_manager_user(record.manager_name, name_index)
        if username and _manager_user_allowed(username, record.office_code):
            record.manager_user = username
            ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
            resolved += 1
    db.session.commit()
    return jsonify({"success": True, "resolved": resolved})


# ============================================================
# 添付ファイル
# ============================================================

@health_check_bp.route("/api/record/<int:record_id>/attachment", methods=["POST"])
@login_required
def api_upload_attachment(record_id):
    user_id = str(current_user.username)
    record = db.session.get(HealthCheckRecord, record_id)
    if not record:
        return jsonify({"error": "レコードが見つかりません"}), 404
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403

    if "file" not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    if not _allowed_attachment(file.filename):
        return jsonify({"error": "pdf / jpg / png のみアップロードできます"}), 400

    category = (request.form.get("category") or "health").strip()
    if category not in ATTACHMENT_CATEGORIES:
        category = "health"

    title = _clean_attachment_title(request.form.get("title"))

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_ATTACHMENT_SIZE:
        return jsonify({"error": "ファイルサイズは10MBまでです"}), 400

    import uuid
    ext = file.filename.rsplit(".", 1)[1].lower()
    year_dir = str(record.target_year)
    rel_path = os.path.join(year_dir, f"{uuid.uuid4().hex}.{ext}")
    abs_dir = os.path.join(get_uploads_path(), year_dir)
    os.makedirs(abs_dir, exist_ok=True)
    file.save(os.path.join(get_uploads_path(), rel_path))

    attachment = HealthCheckAttachment(
        record_id=record.id,
        category=category,
        title=title,
        stored_path=rel_path,
        original_name=_safe_original_name(file.filename, ext),
        # 申告値は記録として残すだけ。配信時の Content-Type は拡張子から決める。
        content_type=ATTACHMENT_MIME_BY_EXT.get(ext, "application/octet-stream"),
        file_size=size,
        uploaded_by=user_id,
    )
    db.session.add(attachment)
    db.session.commit()
    return jsonify({"success": True, "attachment": attachment.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>/attachment/<int:attachment_id>")
@login_required
def api_download_attachment(record_id, attachment_id):
    user_id = str(current_user.username)
    attachment = db.session.get(HealthCheckAttachment, attachment_id)
    if not attachment or attachment.record_id != record_id:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    record = db.session.get(HealthCheckRecord, record_id)
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403
    abs_path = os.path.join(get_uploads_path(), attachment.stored_path)
    if not os.path.exists(abs_path):
        return jsonify({"error": "ファイルが見つかりません"}), 404
    # inline=1 のときはプレビュー用にブラウザ内表示（画像/PDF）。既定はダウンロード。
    inline = request.args.get("inline") == "1"
    # Content-Type は保存済みの拡張子から決める（クライアント申告値は信用しない）。
    ext = attachment.stored_path.rsplit(".", 1)[-1].lower() if "." in attachment.stored_path else ""
    mimetype = ATTACHMENT_MIME_BY_EXT.get(ext)
    if mimetype is None:
        # 想定外の拡張子はブラウザで開かせず、必ずダウンロードさせる。
        mimetype, inline = "application/octet-stream", False
    response = send_file(
        abs_path,
        as_attachment=not inline,
        download_name=_attachment_download_name(attachment, ext),
        mimetype=mimetype,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@health_check_bp.route("/api/record/<int:record_id>/attachment/<int:attachment_id>", methods=["PATCH"])
@login_required
def api_update_attachment(record_id, attachment_id):
    """添付ファイルのタイトルを更新する。"""
    user_id = str(current_user.username)
    attachment = db.session.get(HealthCheckAttachment, attachment_id)
    if not attachment or attachment.record_id != record_id:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    record = db.session.get(HealthCheckRecord, record_id)
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403
    data = request.get_json(silent=True) or {}
    attachment.title = _clean_attachment_title(data.get("title"))
    db.session.commit()
    return jsonify({"success": True, "attachment": attachment.to_dict()})


@health_check_bp.route("/api/record/<int:record_id>/attachment/<int:attachment_id>", methods=["DELETE"])
@login_required
def api_delete_attachment(record_id, attachment_id):
    user_id = str(current_user.username)
    attachment = db.session.get(HealthCheckAttachment, attachment_id)
    if not attachment or attachment.record_id != record_id:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    record = db.session.get(HealthCheckRecord, record_id)
    if not _check_record_access(user_id, record):
        return jsonify({"error": "アクセス権限がありません"}), 403
    try:
        os.remove(os.path.join(get_uploads_path(), attachment.stored_path))
    except OSError:
        pass
    db.session.delete(attachment)
    db.session.commit()
    return jsonify({"success": True})


# ============================================================
# 名簿候補・ユーザー候補・ダッシュボード
# ============================================================

@health_check_bp.route("/api/employees")
@login_required
def api_employees():
    """名簿連携レコード作成用の社員候補（自分のスコープ内・在籍者）。"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)
    if not user_offices:
        return jsonify({"employees": []})
    search = request.args.get("search", "").strip()
    query = Employee.query.filter(
        Employee.office_code.in_(user_offices),
        Employee.is_deleted.is_(False),
    )
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Employee.employee_name.like(like),
            Employee.employee_number.like(like),
        ))
    employees = query.order_by(Employee.employee_number).limit(50).all()
    return jsonify({"employees": [{
        "id": e.id,
        "employee_number": e.employee_number,
        "employee_name": e.employee_name,
        "office_code": e.office_code,
        "office_name": e.office_name,
        "company_name": e.company_name,
        "manager_name": e.manager_name,
        "birth_date": e.birth_date.isoformat() if e.birth_date else None,
    } for e in employees]})


@health_check_bp.route("/api/users")
@login_required
def api_users():
    """担当者割当用の DSTTユーザー候補。"""
    user_id = str(current_user.username)
    query = User.query
    allowed_offices = None
    if not is_dstt_admin():
        allowed_offices = set(get_user_offices(user_id))
        if not allowed_offices:
            return jsonify({"users": []})
    users = query.order_by(User.name, User.username).all()
    if allowed_offices is not None:
        users = [u for u in users if set(_dstt_user_office_codes(u)) & allowed_offices]
    return jsonify({"users": [{"username": u.username, "name": u.name or u.username} for u in users]})


@health_check_bp.route("/api/area_managers")
@login_required
def api_area_managers():
    """管理担当の候補。対象営業所に所属し、担当（AccessDepartment）が
    「エリアマネージャー」に設定されているDSTTユーザー。
    手動モード（入社前・内勤者）のレコード追加時に管理担当を選ぶために使う。"""
    user_id = str(current_user.username)
    user_offices = get_user_offices(user_id)
    if not user_offices:
        return jsonify({"managers": []})
    # 指定営業所（アクセス可能なもの）に限定。未指定なら自分のスコープ全体。
    office = request.args.get("office", "").strip()
    offices = [office] if office and has_office_access(user_id, office) else user_offices

    # 営業所コード → アクセス権営業所(AccessOffice) → その営業所の「エリアマネージャー」担当
    office_ids = [o.id for o in AccessOffice.query.filter(AccessOffice.code.in_(offices)).all()]
    if not office_ids:
        return jsonify({"managers": []})
    dept_ids = [
        d.id for d in AccessDepartment.query.filter(
            AccessDepartment.office_id.in_(office_ids),
            AccessDepartment.name == AREA_MANAGER_DEPARTMENT,
        ).all()
    ]
    if not dept_ids:
        return jsonify({"managers": []})

    users = (
        User.query.filter(User.department_id.in_(dept_ids))
        .order_by(User.name, User.username)
        .all()
    )
    return jsonify({"managers": [{
        "username": u.username,
        "name": u.name or u.username,
    } for u in users]})


@health_check_bp.route("/api/sites")
@login_required
def api_sites():
    """専従先名の検索候補（現場リストPLUS＝Site）。手動入力も可能なので候補提示のみ。"""
    user_id = str(current_user.username)
    search = request.args.get("search", "").strip()
    query = Site.query.filter(Site.is_active.is_(True))
    # 現場（専従先）候補も自分の営業所スコープに限る。以前は全営業所の現場を
    # 誰でも列挙できた。
    if not is_dstt_admin():
        user_offices = get_user_offices(user_id)
        if not user_offices:
            return jsonify({"sites": []})
        query = query.filter(Site.office_code.in_(user_offices))
    if search:
        query = query.filter(Site.site_name.like(f"%{search}%"))
    sites = query.order_by(Site.site_name).limit(50).all()
    # 同名現場（複数支店等）はまとめて1件に
    seen: set[str] = set()
    result = []
    for s in sites:
        if s.site_name in seen:
            continue
        seen.add(s.site_name)
        result.append({"site_id": s.site_id, "site_name": s.site_name})
    return jsonify({"sites": result})


@health_check_bp.route("/api/dashboard")
@login_required
def api_dashboard():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error
    all_records = query.all()
    _sync_record_list(all_records)
    # 受診非対象者はヒーローエリアの各カウント対象外
    records = [r for r in all_records if not r.is_exempt]
    exempt = len(all_records) - len(records)
    status_counts: dict[str, int] = {}
    night_pending = 0
    unassigned = 0
    for record in records:
        status = record.compute_status()
        status_counts[status] = status_counts.get(status, 0) + 1
        if record.is_night_worker and not record.exam_date_2:
            night_pending += 1
        if not record.manager_user:
            unassigned += 1

    total = len(records)
    examined = status_counts.get("受診済", 0) + status_counts.get("再検査対象", 0) \
        + status_counts.get("二次案内済", 0) + status_counts.get("二次完了", 0)
    return jsonify({
        "total": total,
        "examined": examined,
        "exam_rate": round(examined / total * 100, 1) if total else 0.0,
        "status_counts": status_counts,
        "recheck_pending": status_counts.get("再検査対象", 0) + status_counts.get("二次案内済", 0),
        "night_pending": night_pending,
        "unassigned": unassigned,
        "exempt": exempt,
    })


@health_check_bp.route("/api/years")
@login_required
def api_years():
    user_id = str(current_user.username)
    query = (
        db.session.query(HealthCheckRecord.target_year)
        .outerjoin(Employee, HealthCheckRecord.employee_id == Employee.id)
        .distinct()
    )
    if not is_dstt_admin():
        user_offices = get_user_offices(user_id)
        query = query.filter(db.or_(
            db.and_(
                HealthCheckRecord.record_type == "linked",
                HealthCheckRecord.employee_id.isnot(None),
                Employee.id.isnot(None),
                Employee.office_code.in_(user_offices or ["__none__"]),
            ),
            db.and_(
                db.or_(
                    HealthCheckRecord.record_type != "linked",
                    HealthCheckRecord.employee_id.is_(None),
                    Employee.id.is_(None),
                ),
                HealthCheckRecord.office_code.in_(user_offices or ["__none__"]),
            ),
        ))
    years = sorted({row[0] for row in query.all()}, reverse=True)
    return jsonify({"years": years})


@health_check_bp.route("/api/history")
@login_required
def api_history():
    record_id = request.args.get("record_id", type=int)
    if record_id:
        record = db.session.get(HealthCheckRecord, record_id)
        if not _check_record_access(str(current_user.username), record):
            return jsonify({"error": "アクセス権限がありません"}), 403
        rows = (HealthCheckEditHistory.query.filter_by(record_id=record_id)
                .order_by(HealthCheckEditHistory.edited_at.desc()).limit(100).all())
    elif is_dstt_admin():
        # 全営業所横断の履歴閲覧は DSTT管理者のみ
        rows = (HealthCheckEditHistory.query
                .order_by(HealthCheckEditHistory.edited_at.desc()).limit(100).all())
    else:
        return jsonify({"histories": []})
    return jsonify({"histories": [h.to_dict() for h in rows]})


# ============================================================
# エクスポート
# ============================================================

_EXPORT_COLUMNS = [
    ("target_year", "健診年度"),
    ("record_type", "区分"),
    ("employee_number", "社員番号"),
    ("employee_name", "社員名"),
    ("employee_type", "社員区分"),
    ("assignment_site", "専従先名"),
    ("manager_name", "管理担当名"),
    ("hire_date", "入社日付"),
    ("retirement_date", "退職日付"),
    ("is_retired", "退職者"),
    ("reservation_date", "予約日"),
    ("exam_date", "受診日"),
    ("exam_date_2", "受診日②(深夜2回目)"),
    ("medical_institution", "受診医療機関名"),
    ("is_night_worker", "深夜従事者"),
    ("needs_recheck", "再検査有無"),
    ("recheck_items", "再検査項目"),
    ("secondary_recommended_date", "二次検査受診推奨日"),
    ("secondary_exam_date", "二次検診受診日"),
    ("secondary_guide_sent_date", "二次検査案内送付日"),
    ("secondary_result", "二次検査結果"),
    ("birth_date", "生年月日"),
    ("nasva_reservation_date", "NASVA予約日"),
    ("nasva_exam_date", "NASVA受診日"),
    ("is_exempt", "受診非対象"),
    ("is_kintone", "kintone"),
    ("status", "受診ステータス"),
    ("remarks", "備考"),
]
_EXPORT_BOOL_KEYS = {"is_night_worker", "needs_recheck", "is_retired", "is_exempt", "is_kintone"}
_RECORD_TYPE_LABEL = {"linked": "名簿連携", "pre_hire": "入社前", "internal": "内勤者"}

# CSV の追加絞り込みで利用できる項目。クライアントからモデル属性名を自由に
# 指定させず、型と演算子をサーバ側の許可リストで固定する。
_EXPORT_FILTER_FIELDS = {
    "status": "status",
    "record_type": "enum",
    "employee_number": "text",
    "employee_name": "text",
    "employee_type": "text",
    "assignment_site": "text",
    "manager_name": "text",
    "medical_institution": "text",
    "recheck_items": "text",
    "secondary_result": "text",
    "remarks": "text",
    "hire_date": "date",
    "birth_date": "date",
    "reservation_date": "date",
    "exam_date": "date",
    "exam_date_2": "date",
    "secondary_recommended_date": "date",
    "secondary_guide_sent_date": "date",
    "secondary_exam_date": "date",
    "nasva_reservation_date": "date",
    "nasva_exam_date": "date",
    "is_night_worker": "bool",
    "needs_recheck": "bool",
    "is_exempt": "bool",
    "is_kintone": "bool",
    "is_retired": "bool",
}
_EXPORT_TEXT_OPERATORS = {"contains", "not_contains", "equals", "not_equals", "empty", "not_empty"}
_EXPORT_DATE_OPERATORS = {"on", "before", "after", "on_or_before", "on_or_after", "empty", "not_empty"}
_EXPORT_ENUM_OPERATORS = {"equals", "not_equals"}
_EXPORT_BOOL_OPERATORS = {"is_true", "is_false"}
_EXPORT_STATUS_VALUES = {"未予約", "予約済", "受診済", "再検査対象", "二次案内済", "二次完了"}


def _parse_export_filters():
    """追加のCSV絞り込み条件を検証し、正規化した配列を返す。"""
    raw = request.args.get("export_filters", "").strip()
    if not raw:
        return [], None
    try:
        filters = json.loads(raw)
    except (TypeError, ValueError):
        return None, "CSV出力条件の形式が不正です"
    if not isinstance(filters, list) or len(filters) > 20:
        return None, "CSV出力条件は20件以内で指定してください"

    normalized = []
    for item in filters:
        if not isinstance(item, dict):
            return None, "CSV出力条件の形式が不正です"
        field = str(item.get("field", "")).strip()
        operator = str(item.get("operator", "")).strip()
        kind = _EXPORT_FILTER_FIELDS.get(field)
        allowed = {
            "text": _EXPORT_TEXT_OPERATORS,
            "date": _EXPORT_DATE_OPERATORS,
            "enum": _EXPORT_ENUM_OPERATORS,
            "bool": _EXPORT_BOOL_OPERATORS,
            "status": _EXPORT_ENUM_OPERATORS,
        }.get(kind, set())
        if not kind or operator not in allowed:
            return None, "CSV出力条件に利用できない項目または比較方法があります"

        value = str(item.get("value", "")).strip()
        needs_value = operator not in {"empty", "not_empty", "is_true", "is_false"}
        if needs_value and not value:
            return None, "CSV出力条件の値を入力してください"
        if kind == "date" and needs_value:
            try:
                value = date.fromisoformat(value).isoformat()
            except ValueError:
                return None, "CSV出力条件の日付が不正です"
        if kind == "status" and value not in _EXPORT_STATUS_VALUES:
            return None, "CSV出力条件のステータスが不正です"
        if kind == "enum" and field == "record_type" and value not in _RECORD_TYPE_LABEL:
            return None, "CSV出力条件の区分が不正です"
        normalized.append({"field": field, "operator": operator, "value": value, "kind": kind})
    return normalized, None


def _apply_export_query_filters(query, filters):
    """DB列で評価できるCSV追加条件を適用する。ステータスは取得後に評価する。"""
    for item in filters:
        if item["kind"] == "status":
            continue
        column = getattr(HealthCheckRecord, item["field"])
        operator = item["operator"]
        value = item["value"]
        if operator == "empty":
            query = query.filter(
                column.is_(None) if item["kind"] == "date"
                else db.or_(column.is_(None), column == "")
            )
        elif operator == "not_empty":
            query = query.filter(
                column.isnot(None) if item["kind"] == "date"
                else db.and_(column.isnot(None), column != "")
            )
        elif item["kind"] == "bool":
            query = query.filter(column.is_(operator == "is_true"))
        elif item["kind"] == "date":
            parsed = date.fromisoformat(value)
            comparisons = {
                "on": column == parsed,
                "before": column < parsed,
                "after": column > parsed,
                "on_or_before": column <= parsed,
                "on_or_after": column >= parsed,
            }
            query = query.filter(comparisons[operator])
        elif operator == "contains":
            escaped = value.replace("/", "//").replace("%", "/%").replace("_", "/_")
            query = query.filter(column.ilike(f"%{escaped}%", escape="/"))
        elif operator == "not_contains":
            escaped = value.replace("/", "//").replace("%", "/%").replace("_", "/_")
            query = query.filter(db.or_(
                column.is_(None),
                ~column.ilike(f"%{escaped}%", escape="/"),
            ))
        elif operator == "equals":
            query = query.filter(column == value)
        elif operator == "not_equals":
            query = query.filter(db.or_(column.is_(None), column != value))
    return query


def _matches_export_status_filters(record, filters) -> bool:
    status = record.compute_status()
    for item in filters:
        if item["kind"] != "status":
            continue
        if item["operator"] == "equals" and status != item["value"]:
            return False
        if item["operator"] == "not_equals" and status == item["value"]:
            return False
    return True


def _csv_safe(value) -> str:
    """Excel の数式インジェクション対策。

    備考・二次検査結果などの自由入力が `=`/`+`/`-`/`@`/TAB/CR で始まると、
    Excel が数式として解釈・実行する。先頭に `'` を付けて無害化する
    （CloudShift のエクスポートと同じ方針）。
    """
    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@health_check_bp.route("/api/export")
@login_required
def api_export():
    user_id = str(current_user.username)
    query, error = _scoped_query(user_id)
    if error:
        return error

    export_filters, filter_error = _parse_export_filters()
    if filter_error:
        return jsonify({"error": filter_error}), 400
    query = _apply_export_query_filters(query, export_filters)

    sort_by = request.args.get("sort_by", "employee_number")
    sort_order = request.args.get("sort_order", "asc")
    if sort_by not in SORTABLE_FIELDS:
        sort_by = "employee_number"
    sort_column = getattr(HealthCheckRecord, sort_by)
    query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())

    records = query.all()
    _sync_record_list(records)
    status_filter = request.args.get("status", "").strip()
    if status_filter:
        records = [r for r in records if r.compute_status() == status_filter]
    records = [r for r in records if _matches_export_status_filters(r, export_filters)]
    year = request.args.get("year", type=int)

    selected_columns = _EXPORT_COLUMNS
    raw_columns = request.args.get("export_columns", "").strip()
    if raw_columns:
        allowed_columns = dict(_EXPORT_COLUMNS)
        selected_keys = list(dict.fromkeys(k.strip() for k in raw_columns.split(",") if k.strip()))
        if not selected_keys or any(key not in allowed_columns for key in selected_keys):
            return jsonify({"error": "CSV出力項目が不正です"}), 400
        selected_columns = [(key, allowed_columns[key]) for key in selected_keys]

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in selected_columns])
    for record in records:
        data = record.to_dict()
        row = []
        for key, _ in selected_columns:
            if key == "record_type":
                row.append(_RECORD_TYPE_LABEL.get(record.record_type, record.record_type))
            elif key in _EXPORT_BOOL_KEYS:
                row.append("○" if data.get(key) else "")
            elif key == "recheck_items":
                row.append(_csv_safe(record.recheck_items_text()))
            else:
                row.append(_csv_safe(data.get(key)))
        writer.writerow(row)

    output = BytesIO(buffer.getvalue().encode("utf-8-sig"))
    output.seek(0)
    filename = f"健診PLUS_{year or 'all'}_{datetime.now().strftime('%Y%m%d')}.csv"
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name=filename)


# ============================================================
# 設定・連携・管理
# ============================================================

@health_check_bp.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    user_id = str(current_user.username)
    if request.method == "POST":
        # ツール全体の設定変更は DSTT管理者のみ
        if not is_dstt_admin():
            return jsonify({"error": "管理者権限が必要です"}), 403
        payload = request.json or {}
        settings = load_settings()
        if "global_lead_days" in payload:
            try:
                settings["global_lead_days"] = max(0, int(payload["global_lead_days"]))
            except (TypeError, ValueError):
                return jsonify({"error": "リードタイムが不正です"}), 400
        save_settings(settings)
        return jsonify({"success": True, "settings": settings})

    # GET も DSTT管理者のみ。settings.json には health_officers
    # （営業所コード → 健康診断担当の username 配列）が入っており、
    # 以前は @login_required だけで全営業所分を誰にでも返していた。
    # 自営業所の担当者一覧が必要な場合は /api/office_officers を使う。
    if not is_dstt_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    return jsonify({"settings": load_settings()})


@health_check_bp.route("/api/integration", methods=["GET", "POST"])
@login_required
def api_integration():
    """ログインユーザー個人の ToBell 連携設定（オプトイン＋通知種別の個別ON/OFF）。"""
    from app.services.to_bell_integrations import (
        get_settings,
        update_integrations,
        get_health_check_notify,
        set_health_check_notify,
    )
    user_id = str(current_user.username)
    if request.method == "POST":
        payload = request.json or {}
        enabled = bool(payload.get("enabled"))
        result = update_integrations(user_id, {"integrations": {"health_check.linkage": enabled}})
        kinds = payload.get("kinds")
        notify = set_health_check_notify(user_id, kinds) if isinstance(kinds, dict) else get_health_check_notify(user_id)
        # 前日に作成済みのタスクも、個人のON/OFF変更へ即時追従させる。
        sync_result = resync_health_check_recipient(user_id)
        return jsonify({
            "success": True,
            "enabled": result["integrations"].get("health_check.linkage", False),
            "kinds": notify,
            "warning": ("作成済み通知への反映に失敗しました。日次同期で再試行します。"
                        if sync_result.get("failed") else None),
        })
    settings = get_settings(user_id)
    return jsonify({
        "enabled": settings["integrations"].get("health_check.linkage", False),
        "kinds": get_health_check_notify(user_id),
    })


@health_check_bp.route("/api/admin/permissions")
@login_required
def api_admin_permissions():
    """健診PLUS管理者の一覧。営業所スコープはDSTTのアクセス権に同期するため、
    ここでは管理者(admins)の指定のみを扱う。DSTT管理者のみ閲覧・変更できる。"""
    if not is_dstt_admin():
        return jsonify({"error": "DSTT管理者権限が必要です"}), 403
    permissions = load_permissions()
    result = []
    for uid in sorted(set(permissions.get("admins", []))):
        user = User.query.filter_by(username=uid).first()
        result.append({
            "user_id": uid,
            "name": (user.name if user and user.name else uid),
            "offices": sorted(get_user_offices(uid)),
        })
    return jsonify({"users": result})


@health_check_bp.route("/api/admin/grant", methods=["POST"])
@login_required
def api_admin_grant():
    """ユーザーを健診PLUS管理者に指定する（DSTT管理者のみ）。
    営業所アクセスはDSTTのアクセス権に同期するため、ここでは付与しない。"""
    if not is_dstt_admin():
        return jsonify({"error": "DSTT管理者権限が必要です"}), 403
    payload = request.json or {}
    target = (payload.get("user_id") or "").strip()
    if not target:
        return jsonify({"error": "ユーザーIDが必要です"}), 400
    if not User.query.filter_by(username=target).first():
        return jsonify({"error": "指定のユーザーが存在しません"}), 400
    permissions = load_permissions()
    if target not in permissions.setdefault("admins", []):
        permissions["admins"].append(target)
    save_permissions(permissions)
    return jsonify({"success": True})


@health_check_bp.route("/api/admin/revoke", methods=["POST"])
@login_required
def api_admin_revoke():
    """健診PLUS管理者の指定を解除する（DSTT管理者のみ）。"""
    if not is_dstt_admin():
        return jsonify({"error": "DSTT管理者権限が必要です"}), 403
    payload = request.json or {}
    target = (payload.get("user_id") or "").strip()
    permissions = load_permissions()
    if target in permissions.get("admins", []):
        permissions["admins"].remove(target)
    save_permissions(permissions)
    return jsonify({"success": True})


# ============================================================
# 健康診断担当（営業所ごとの既定通知先）
# ============================================================

def _officer_user_label(username: str) -> str:
    user = User.query.filter_by(username=username).first()
    return (user.name if user and user.name else username)


@health_check_bp.route("/api/office_officers")
@login_required
def api_office_officers():
    """営業所の健康診断担当（既定の通知先）を返す。レコード編集の宛先表示に使う。"""
    user_id = str(current_user.username)
    office = request.args.get("office", "").strip()
    if office and not has_office_access(user_id, office):
        return jsonify({"officers": []})
    officers = get_office_health_officers(office)
    return jsonify({"officers": [
        {"username": u, "name": _officer_user_label(u)} for u in officers
    ]})


# ============================================================
# 本人あてメール通知のコントロールパネル
# ============================================================

def _mail_notify_table(settings: dict) -> dict:
    table = settings.get("mail_notify")
    return table if isinstance(table, dict) else {}


def _save_mail_notify(office_codes: list[str], enabled: bool, kinds: dict) -> list[str]:
    """指定営業所のメール送信設定を保存し、実際に適用した営業所コードを返す。"""
    settings = load_settings()
    table = _mail_notify_table(settings)
    applied = []
    for code in office_codes:
        table[code] = normalize_mail_notify({"enabled": enabled, "kinds": kinds})
        applied.append(code)
    settings["mail_notify"] = table
    save_settings(settings)
    return applied


def _resync_records_for_mail_offices(office_codes: list[str]) -> int:
    """本人メール設定の変更を、その営業所の未送信キューへ即時反映する。"""
    codes = {str(code).strip() for code in office_codes if str(code).strip()}
    if not codes:
        return 0
    try:
        records = HealthCheckRecord.query.outerjoin(
            Employee, HealthCheckRecord.employee_id == Employee.id
        ).filter(db.or_(
            db.and_(
                HealthCheckRecord.record_type == "linked",
                HealthCheckRecord.employee_id.isnot(None),
                Employee.id.isnot(None),
                Employee.office_code.in_(codes),
            ),
            db.and_(
                db.or_(
                    HealthCheckRecord.record_type != "linked",
                    HealthCheckRecord.employee_id.is_(None),
                    Employee.id.is_(None),
                ),
                HealthCheckRecord.office_code.in_(codes),
            ),
        )).all()
        failed = 0
        for record in records:
            if not ensure_health_check_reminders(
                record, global_lead_days=get_global_lead_days(), commit=False
            ):
                failed += 1
        db.session.commit()
        return failed
    except Exception:
        db.session.rollback()
        return 1


@health_check_bp.route("/api/admin/mail_notify", methods=["GET", "POST"])
@login_required
def api_admin_mail_notify():
    """営業所ごとの本人あてメール送信設定の取得・変更（個別）。

    社員はDSTTアカウントを持たないため本人のオプトインが存在しない。
    送信可否はここでの管理者設定だけで決まるので、既定は必ず「送らない」。
    """
    user_id = str(current_user.username)
    if not is_health_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    if request.method == "POST":
        payload = request.json or {}
        office_code = (payload.get("office_code") or "").strip()
        if not office_code:
            return jsonify({"error": "営業所コードが必要です"}), 400
        if not can_admin_office(user_id, office_code):
            return jsonify({"error": "この営業所を管理する権限がありません"}), 403
        applied = _save_mail_notify(
            [office_code], bool(payload.get("enabled")), payload.get("kinds") or {})
        sync_failed = _resync_records_for_mail_offices(applied)
        record_history(None, user_id, "mail_notify", office_code, None,
                       json.dumps(get_office_mail_notify(office_code), ensure_ascii=False))
        db.session.commit()
        return jsonify({"success": True, "office_code": applied[0],
                        "setting": get_office_mail_notify(office_code),
                        "warning": ("既存の送信予定への反映に失敗しました。日次同期で再試行します。"
                                    if sync_failed else None)})

    result = []
    for o in _office_options():
        if not can_admin_office(user_id, o["code"]):
            continue
        setting = get_office_mail_notify(o["code"])
        result.append({"code": o["code"], "name": o["name"], **setting})
    return jsonify({"offices": result, "kind_labels": MAIL_KIND_LABELS})


@health_check_bp.route("/api/admin/mail_notify/bulk", methods=["POST"])
@login_required
def api_admin_mail_notify_bulk():
    """管理できる営業所すべて（または指定分）へまとめて適用する。"""
    user_id = str(current_user.username)
    if not is_health_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    payload = request.json or {}
    requested = payload.get("offices")
    candidates = [o["code"] for o in _office_options() if can_admin_office(user_id, o["code"])]
    if isinstance(requested, list) and requested:
        targets = [c for c in candidates if c in {str(x).strip() for x in requested}]
    else:
        targets = candidates
    if not targets:
        return jsonify({"error": "対象営業所がありません"}), 403

    applied = _save_mail_notify(targets, bool(payload.get("enabled")), payload.get("kinds") or {})
    sync_failed = _resync_records_for_mail_offices(applied)
    record_history(None, user_id, "mail_notify_bulk", ",".join(applied), None,
                   json.dumps({"enabled": bool(payload.get("enabled")),
                               "kinds": payload.get("kinds") or {}}, ensure_ascii=False))
    db.session.commit()
    return jsonify({"success": True, "applied": applied,
                    "warning": ("一部の既存送信予定への反映に失敗しました。日次同期で再試行します。"
                                if sync_failed else None)})


def _mail_basis_for(record, kind: str):
    """種別ごとの対象日と、通知条件を満たしているかを返す。"""
    if kind == "reservation":
        return record.reservation_date, (record.reservation_date is not None
                                         and record.exam_date is None)
    if kind == "night_second":
        basis = record.exam_date_2_target
        if basis is None and record.exam_date is not None:
            basis = record.exam_date + relativedelta(months=6)
        return basis, (bool(record.is_night_worker) and record.exam_date_2 is None)
    if kind == "secondary_exam":
        return record.secondary_recommended_date, (
            bool(record.needs_recheck)
            and record.secondary_recommended_date is not None
            and record.secondary_exam_date is None)
    return None, False


@health_check_bp.route("/api/mail_preview")
@login_required
def api_mail_preview():
    """送信対象のプレビュー。誰に送られ、誰に送られないか（理由つき）を返す。

    一括送信の前に必ずここで件数と内訳を確認できるようにする。
    """
    from app.services.health_check_mail import can_send_to, build_message

    user_id = str(current_user.username)
    kind = (request.args.get("kind") or "secondary_exam").strip()
    if kind not in MAIL_KINDS:
        return jsonify({"error": "通知種別が不正です"}), 400
    query, error = _scoped_query(user_id)
    if error:
        return error

    records = query.all()
    _sync_record_list(records)

    sendable, blocked, sample = 0, {}, None
    for record in records:
        basis, condition = _mail_basis_for(record, kind)
        if not condition or basis is None:
            continue
        ok, reason = can_send_to(record)
        if not ok:
            blocked[reason] = blocked.get(reason, 0) + 1
            continue
        sendable += 1
        if sample is None:
            subject, body = build_message(
                record, kind, basis,
                office_name=get_office_display_name(record.office_code),
                contact_name=record.manager_name or "")
            sample = {"subject": subject, "body": body,
                      "to_name": record.employee_name, "to": record.employee_email}

    return jsonify({
        "kind": kind,
        "kind_label": MAIL_KIND_LABELS.get(kind, kind),
        "sendable": sendable,
        "blocked": [{"reason": r, "count": c} for r, c in sorted(blocked.items())],
        "sample": sample,
    })


@health_check_bp.route("/api/mail_send", methods=["POST"])
@login_required
def api_mail_send():
    """未受診者への督促などを手動で一括送信する。

    自動送信は対象日当日の1回のみ。受診勧奨を追いかけるには足りないため、
    管理者が任意のタイミングでまとめて送れる導線を用意する。
    dedupe_key に送信日を含めるので、同日の二重送信だけを防ぐ。
    """
    from app.services.health_check_mail import can_send_to, build_message, MAIL_KINDS as _K
    from app.services.mail_service import queue_mail

    user_id = str(current_user.username)
    if not is_health_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    payload = request.json or {}
    kind = (payload.get("kind") or "").strip()
    if kind not in _K:
        return jsonify({"error": "通知種別が不正です"}), 400

    query, error = _scoped_query(user_id)
    if error:
        return error
    records = query.all()
    _sync_record_list(records)

    today = date.today()
    only_ids = payload.get("record_ids")
    only = {int(x) for x in only_ids} if isinstance(only_ids, list) and only_ids else None

    sent, skipped = 0, 0
    for record in records:
        if only is not None and record.id not in only:
            continue
        if not can_admin_office(user_id, _record_access_office(record)):
            continue
        basis, condition = _mail_basis_for(record, kind)
        if not condition or basis is None:
            continue
        ok, _reason = can_send_to(record)
        if not ok:
            skipped += 1
            continue
        subject, body = build_message(
            record, kind, basis,
            office_name=get_office_display_name(record.office_code),
            contact_name=record.manager_name or "")
        queue_mail(
            record.employee_email, subject, body,
            to_name=record.employee_name, category="health_check",
            dedupe_key=f"health_check:manual:{record.id}:{kind}:{today.isoformat()}",
            related_type="health_check_record", related_key=str(record.id),
            created_by=user_id, commit=False,
        )
        sent += 1

    record_history(None, user_id, "mail_send", kind, None, f"{sent}件送信")
    db.session.commit()
    return jsonify({"success": True, "sent": sent, "skipped": skipped})


@health_check_bp.route("/api/admin/health_officers", methods=["GET", "POST"])
@login_required
def api_admin_health_officers():
    """営業所ごとの健康診断担当の取得・設定。
    DSTT管理者は全営業所、健診PLUS管理者は自分の営業所のみ。"""
    user_id = str(current_user.username)
    if not is_health_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403

    settings = load_settings()
    officers_map = settings.get("health_officers")
    if not isinstance(officers_map, dict):
        officers_map = {}

    if request.method == "POST":
        payload = request.json or {}
        office_code = (payload.get("office_code") or "").strip()
        if not office_code:
            return jsonify({"error": "営業所コードが必要です"}), 400
        # 自分が管理できる営業所か（DSTT管理者は全営業所、健診PLUS管理者は自営業所のみ）
        if not can_admin_office(user_id, office_code):
            return jsonify({"error": "この営業所を管理する権限がありません"}), 403
        raw_users = payload.get("users")
        if not isinstance(raw_users, list):
            return jsonify({"error": "担当者の指定が不正です"}), 400
        usernames: list[str] = []
        for entry in raw_users:
            u = str(entry).strip()
            if u and u not in usernames:
                usernames.append(u)
        # 実在するだけでなく「その営業所にアクセスできる」ユーザーだけを担当に据える。
        # 健康診断担当はその営業所の全レコードの既定通知先になるため、営業所外の
        # ユーザーを指定できると、健診PLUSを閲覧できない相手に氏名＋二次検査対象
        # という健康情報が ToBell 経由で配信されてしまう。
        candidates = User.query.filter(User.username.in_(usernames)).all() if usernames else []
        valid = {u.username for u in candidates if _manager_user_allowed(u.username, office_code)}
        rejected = [u for u in usernames if u not in valid]
        filtered = [u for u in usernames if u in valid]
        if filtered:
            officers_map[office_code] = filtered
        else:
            officers_map.pop(office_code, None)
        settings["health_officers"] = officers_map
        save_settings(settings)
        # 当該営業所のレコードは既定宛先が変わるため、リマインドを再同期する
        try:
            records = HealthCheckRecord.query.outerjoin(
                Employee, HealthCheckRecord.employee_id == Employee.id
            ).filter(db.or_(
                db.and_(
                    HealthCheckRecord.record_type == "linked",
                    HealthCheckRecord.employee_id.isnot(None),
                    Employee.id.isnot(None),
                    Employee.office_code == office_code,
                ),
                db.and_(
                    db.or_(
                        HealthCheckRecord.record_type != "linked",
                        HealthCheckRecord.employee_id.is_(None),
                        Employee.id.is_(None),
                    ),
                    HealthCheckRecord.office_code == office_code,
                ),
            )).all()
            for record in records:
                ensure_health_check_reminders(record, global_lead_days=get_global_lead_days(), commit=False)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({"success": True, "office_code": office_code,
                        "officers": [{"username": u, "name": _officer_user_label(u)} for u in filtered],
                        "rejected": rejected,
                        "warning": (
                            f"この営業所へのアクセス権が無いため除外しました: {', '.join(rejected)}"
                            if rejected else None
                        )})

    # 自分が管理できる営業所のみ返す（DSTT管理者は全営業所）
    result = []
    for o in _office_options():
        if not can_admin_office(user_id, o["code"]):
            continue
        result.append({
            "code": o["code"],
            "name": o["name"],
            "officers": [{"username": u, "name": _officer_user_label(u)}
                         for u in get_office_health_officers(o["code"])],
        })
    return jsonify({"offices": result})
