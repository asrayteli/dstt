from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
import os
import json
from datetime import datetime
import re
from pathlib import Path

leave_mgr_bp = Blueprint("leave_mgr", __name__, url_prefix="/tools/leave_mgr")

# 初期管理者ID（ハードコーディング）
INITIAL_ADMIN_ID = "3243012"

# 休暇種類と色の定義
LEAVE_TYPES = {
    "有休": "#DC2626",        # 赤系
    "代休": "#10B981",        # 緑系
    "慶弔休暇": "#8B5CF6",    # 紫系
    "介護休暇": "#F97316",    # オレンジ系
    "リフレッシュ休暇": "#EC4899",  # ピンク系
    "その他": "#0EA5E9"       # 青系
}

def get_data_path():
    """データディレクトリのパスを取得"""
    return os.path.join(current_app.root_path, 'static', 'leave_mgr')

def ensure_data_directories():
    """必要なディレクトリとファイルを作成"""
    data_path = get_data_path()
    calendars_path = os.path.join(data_path, 'calendars')
    
    # ディレクトリ作成
    os.makedirs(calendars_path, exist_ok=True)
    
    # permissions.jsonの初期化
    permissions_file = os.path.join(data_path, 'permissions.json')
    if not os.path.exists(permissions_file):
        initial_permissions = {
            "admins": [INITIAL_ADMIN_ID],
            "user_calendars": {
                INITIAL_ADMIN_ID: []
            }
        }
        with open(permissions_file, 'w', encoding='utf-8') as f:
            json.dump(initial_permissions, f, ensure_ascii=False, indent=2)
    
    # calendar_meta.jsonの初期化
    meta_file = os.path.join(data_path, 'calendar_meta.json')
    if not os.path.exists(meta_file):
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def load_permissions():
    """権限情報を読み込み"""
    permissions_file = os.path.join(get_data_path(), 'permissions.json')
    try:
        with open(permissions_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"admins": [INITIAL_ADMIN_ID], "user_calendars": {}}

def save_permissions(permissions):
    """権限情報を保存"""
    permissions_file = os.path.join(get_data_path(), 'permissions.json')
    with open(permissions_file, 'w', encoding='utf-8') as f:
        json.dump(permissions, f, ensure_ascii=False, indent=2)

def load_calendar_meta():
    """カレンダーメタ情報を読み込み"""
    meta_file = os.path.join(get_data_path(), 'calendar_meta.json')
    try:
        with open(meta_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_calendar_meta(meta):
    """カレンダーメタ情報を保存"""
    meta_file = os.path.join(get_data_path(), 'calendar_meta.json')
    with open(meta_file, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    """管理者権限チェック"""
    permissions = load_permissions()
    return user_id in permissions.get('admins', [])

def get_user_calendars(user_id):
    """ユーザーがアクセス可能なカレンダーIDリストを取得"""
    permissions = load_permissions()
    return permissions.get('user_calendars', {}).get(user_id, [])

def validate_calendar_id(calendar_id):
    """カレンダーIDのバリデーション"""
    # 英数字とアンダースコアのみ、最大20文字
    if not re.match(r'^[a-zA-Z0-9_]+$', calendar_id):
        return False, "カレンダーIDは英数字とアンダースコアのみ使用可能です"
    if len(calendar_id) > 20:
        return False, "カレンダーIDは20文字以内にしてください"
    
    # 数値のみの場合はアンダースコアを追加
    if calendar_id.isdigit():
        calendar_id = calendar_id + "_"
    
    return True, calendar_id

def load_calendar_data(calendar_id, year_month):
    """カレンダーデータを読み込み"""
    file_path = os.path.join(get_data_path(), 'calendars', f"{calendar_id}_{year_month}.json")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"leaves": []}

def save_calendar_data(calendar_id, year_month, data):
    """カレンダーデータを保存"""
    calendars_path = os.path.join(get_data_path(), 'calendars')
    os.makedirs(calendars_path, exist_ok=True)
    
    file_path = os.path.join(calendars_path, f"{calendar_id}_{year_month}.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@leave_mgr_bp.route("/")
@login_required
def index():
    """メインページ"""
    ensure_data_directories()
    
    # 現在のユーザーID（usernameを使用）
    user_id = str(current_user.username)
    
    # ユーザーの権限情報
    is_admin_user = is_admin(user_id)
    user_calendars = get_user_calendars(user_id)
    
    # カレンダーメタ情報
    calendar_meta = load_calendar_meta()
    
    return render_template(
        "leave_mgr.html",
        user_id=user_id,
        is_admin=is_admin_user,
        user_calendars=user_calendars,
        calendar_meta=calendar_meta,
        leave_types=LEAVE_TYPES
    )

@leave_mgr_bp.route("/api/calendar/<calendar_id>/<year_month>")
@login_required
def get_calendar(calendar_id, year_month):
    """カレンダーデータを取得"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    data = load_calendar_data(calendar_id, year_month)
    return jsonify(data)

@leave_mgr_bp.route("/api/leave", methods=["POST"])
@login_required
def add_leave():
    """休暇を追加"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    # 重複チェック（force=Trueの場合はスキップ）
    if not data.get('force', False):
        date = data.get('date')
        name = data.get('name')
        existing = [l for l in calendar_data['leaves'] if l['date'] == date and l['name'] == name]
        
        if existing:
            return jsonify({"warning": "同じ名前で同日に登録されています", "existing": existing}), 409
    
    # 新しい休暇を追加
    new_leave = {
        "id": datetime.now().isoformat(),
        "date": data.get('date'),
        "name": data.get('name'),
        "leave_type": data.get('leave_type'),
        "deputies": data.get('deputies', []),
        "remarks": data.get('remarks', ''),
        "created_by": user_id,  # 記入者
        "created_at": datetime.now().isoformat(),
        "confirmed_by": None,  # 確認者（初期値はNone）
        "confirmed_at": None   # 確認日時（初期値はNone）
    }
    
    calendar_data['leaves'].append(new_leave)
    
    # 保存
    save_calendar_data(calendar_id, year_month, calendar_data)
    
    return jsonify({"success": True, "leave": new_leave})

@leave_mgr_bp.route("/api/leave/<leave_id>", methods=["PUT", "DELETE"])
@login_required
def modify_leave(leave_id):
    """休暇を編集または削除"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    if request.method == "DELETE":
        # 削除権限チェック：自分が作成した休暇のみ削除可能
        leave_to_delete = None
        for leave in calendar_data['leaves']:
            if leave['id'] == leave_id:
                leave_to_delete = leave
                break
        
        if not leave_to_delete:
            return jsonify({"error": "休暇が見つかりません"}), 404
        
        # 管理者または作成者のみ削除可能
        if not is_admin(user_id) and leave_to_delete.get('created_by') != user_id:
            return jsonify({"error": "自分が登録した休暇のみ削除できます"}), 403
        
        # 削除実行
        calendar_data['leaves'] = [l for l in calendar_data['leaves'] if l['id'] != leave_id]
    else:
        # 編集
        for leave in calendar_data['leaves']:
            if leave['id'] == leave_id:
                # 編集権限チェック：管理者または作成者のみ編集可能
                if not is_admin(user_id) and leave.get('created_by') != user_id:
                    return jsonify({"error": "自分が登録した休暇のみ編集できます"}), 403
                
                leave.update({
                    "name": data.get('name', leave['name']),
                    "leave_type": data.get('leave_type', leave['leave_type']),
                    "deputies": data.get('deputies', leave['deputies']),
                    "remarks": data.get('remarks', leave['remarks']),
                    "updated_by": user_id,
                    "updated_at": datetime.now().isoformat()
                })
                break
    
    # 保存
    save_calendar_data(calendar_id, year_month, calendar_data)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/leave/<leave_id>/confirm", methods=["POST"])
@login_required
def confirm_leave(leave_id):
    """休暇を確認"""
    user_id = str(current_user.username)
    data = request.json
    
    calendar_id = data.get('calendar_id')
    year_month = data.get('year_month')
    
    # アクセス権限チェック
    if calendar_id not in get_user_calendars(user_id) and not is_admin(user_id):
        return jsonify({"error": "アクセス権限がありません"}), 403
    
    # カレンダーデータを読み込み
    calendar_data = load_calendar_data(calendar_id, year_month)
    
    # 対象の休暇を見つけて確認者を更新
    for leave in calendar_data['leaves']:
        if leave['id'] == leave_id:
            leave['confirmed_by'] = user_id
            leave['confirmed_at'] = datetime.now().isoformat()
            break
    else:
        return jsonify({"error": "休暇が見つかりません"}), 404
    
    # 保存
    save_calendar_data(calendar_id, year_month, calendar_data)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/user/<username>/name")
@login_required
def get_user_name(username):
    """ユーザーの日本語名を取得"""
    from app.models import User
    
    user = User.query.filter_by(username=username).first()
    if user:
        return jsonify({"name": user.name or "unknown"})
    else:
        return jsonify({"name": "unknown"})

@leave_mgr_bp.route("/api/admin/calendar", methods=["POST"])
@login_required
def create_calendar():
    """新しいカレンダーを作成（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    calendar_id = data.get('calendar_id')
    
    # バリデーション
    valid, processed_id = validate_calendar_id(calendar_id)
    if not valid:
        return jsonify({"error": processed_id}), 400
    
    calendar_id = processed_id
    
    # 既存チェック
    meta = load_calendar_meta()
    if calendar_id in meta:
        return jsonify({"error": "このカレンダーIDは既に存在します"}), 400
    
    # メタ情報を保存
    meta[calendar_id] = {
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "name": data.get('name', calendar_id)
    }
    save_calendar_meta(meta)
    
    # 作成者に権限を付与
    permissions = load_permissions()
    if user_id not in permissions['user_calendars']:
        permissions['user_calendars'][user_id] = []
    if calendar_id not in permissions['user_calendars'][user_id]:
        permissions['user_calendars'][user_id].append(calendar_id)
    save_permissions(permissions)
    
    return jsonify({"success": True, "calendar_id": calendar_id})

@leave_mgr_bp.route("/api/admin/grant", methods=["POST"])
@login_required
def grant_permission():
    """ユーザーに権限を付与（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    target_user_id = data.get('user_id')
    calendar_id = data.get('calendar_id')
    grant_type = data.get('grant_type', 'calendar')  # 'calendar' or 'admin'
    
    permissions = load_permissions()
    
    if grant_type == 'admin':
        # 管理者権限を付与
        if target_user_id not in permissions['admins']:
            permissions['admins'].append(target_user_id)
    else:
        # カレンダーアクセス権限を付与
        if target_user_id not in permissions['user_calendars']:
            permissions['user_calendars'][target_user_id] = []
        if calendar_id not in permissions['user_calendars'][target_user_id]:
            permissions['user_calendars'][target_user_id].append(calendar_id)
    
    save_permissions(permissions)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/admin/users")
@login_required
def get_users_permissions():
    """全ユーザーの権限一覧を取得（管理者のみ）"""
    user_id = str(current_user.username)  # 修正: idからusernameに変更
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    permissions = load_permissions()
    calendar_meta = load_calendar_meta()
    
    # ユーザー権限情報を整形
    users_info = []
    all_users = set(permissions.get('user_calendars', {}).keys()) | set(permissions.get('admins', []))
    
    for uid in all_users:
        user_info = {
            "user_id": uid,
            "is_admin": uid in permissions.get('admins', []),
            "is_protected": uid == INITIAL_ADMIN_ID,  # 初期管理者は保護対象
            "calendars": []
        }
        
        for cal_id in permissions.get('user_calendars', {}).get(uid, []):
            cal_name = calendar_meta.get(cal_id, {}).get('name', cal_id)
            user_info['calendars'].append({
                "id": cal_id,
                "name": cal_name
            })
        
        users_info.append(user_info)
    
    return jsonify(users_info)

@leave_mgr_bp.route("/api/admin/revoke", methods=["POST"])
@login_required
def revoke_permission():
    """ユーザーから権限を剥奪（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    target_user_id = data.get('user_id')
    revoke_type = data.get('revoke_type')  # 'admin', 'calendar', 'admin_and_calendars'
    calendar_id = data.get('calendar_id')  # calendar権限剥奪時のみ使用
    
    # 初期管理者の保護
    if target_user_id == INITIAL_ADMIN_ID:
        return jsonify({"error": "初期管理者の権限は変更できません"}), 403
    
    permissions = load_permissions()
    
    if revoke_type == 'admin':
        # 管理者権限のみ剥奪
        if target_user_id in permissions.get('admins', []):
            permissions['admins'].remove(target_user_id)
    
    elif revoke_type == 'admin_and_calendars':
        # 管理者権限 + 全カレンダーアクセス権限を剥奪
        if target_user_id in permissions.get('admins', []):
            permissions['admins'].remove(target_user_id)
        if target_user_id in permissions.get('user_calendars', {}):
            del permissions['user_calendars'][target_user_id]
    
    elif revoke_type == 'calendar':
        # 特定のカレンダーアクセス権限を剥奪
        if target_user_id in permissions.get('user_calendars', {}):
            if calendar_id in permissions['user_calendars'][target_user_id]:
                permissions['user_calendars'][target_user_id].remove(calendar_id)
                # 空のリストになった場合はキーごと削除
                if not permissions['user_calendars'][target_user_id]:
                    del permissions['user_calendars'][target_user_id]
    
    save_permissions(permissions)
    
    return jsonify({"success": True})

@leave_mgr_bp.route("/api/admin/calendar/<calendar_id>", methods=["DELETE"])
@login_required
def delete_calendar(calendar_id):
    """カレンダーを削除（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    # カレンダーメタ情報から削除
    meta = load_calendar_meta()
    if calendar_id not in meta:
        return jsonify({"error": "カレンダーが見つかりません"}), 404
    
    # 休暇データファイルの数をカウント
    calendars_path = os.path.join(get_data_path(), 'calendars')
    data_count = 0
    
    if os.path.exists(calendars_path):
        pattern = f"{calendar_id}_*.json"
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        data_count += len(data.get('leaves', []))
                except:
                    pass
    
    # 削除実行
    del meta[calendar_id]
    save_calendar_meta(meta)
    
    # 関連する権限を削除
    permissions = load_permissions()
    for uid in list(permissions.get('user_calendars', {}).keys()):
        if calendar_id in permissions['user_calendars'][uid]:
            permissions['user_calendars'][uid].remove(calendar_id)
            if not permissions['user_calendars'][uid]:
                del permissions['user_calendars'][uid]
    save_permissions(permissions)
    
    # データファイルを削除
    if os.path.exists(calendars_path):
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    os.remove(file_path)
                except:
                    pass
    
    return jsonify({"success": True, "deleted_data_count": data_count})

@leave_mgr_bp.route("/api/admin/calendar/<calendar_id>/data_count")
@login_required
def get_calendar_data_count(calendar_id):
    """カレンダーの休暇データ件数を取得（管理者のみ）"""
    user_id = str(current_user.username)
    
    if not is_admin(user_id):
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    calendars_path = os.path.join(get_data_path(), 'calendars')
    data_count = 0
    
    if os.path.exists(calendars_path):
        for filename in os.listdir(calendars_path):
            if filename.startswith(f"{calendar_id}_") and filename.endswith('.json'):
                file_path = os.path.join(calendars_path, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        data_count += len(data.get('leaves', []))
                except:
                    pass
    
    return jsonify({"data_count": data_count})