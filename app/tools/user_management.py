# app/tools/user_management.py

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from app.models import db, User
import re

user_management_bp = Blueprint("user_management", __name__, url_prefix="/tools/user_management")

def is_admin():
    """管理者権限チェック - ログインID 3243012 のみ管理者"""
    return current_user.is_authenticated and current_user.username == "3243012"

@user_management_bp.route("/api/users", methods=["GET"])
@login_required
def get_users():
    """全ユーザー一覧取得（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    users = User.query.all()
    user_list = []
    for user in users:
        user_list.append({
            "id": user.id,
            "username": user.username,
            "name": user.name or "unknown"
        })
    
    return jsonify({"users": user_list})

@user_management_bp.route("/api/users", methods=["POST"])
@login_required
def create_user():
    """新規ユーザー作成（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    data = request.json
    username = data.get('username', '').strip()
    name = data.get('name', '').strip()
    password = data.get('password', '').strip()
    
    # バリデーション
    if not username:
        return jsonify({"error": "ユーザーIDは空にできません"}), 400
    
    if not name:
        return jsonify({"error": "日本語名は空にできません"}), 400
    
    if not password:
        return jsonify({"error": "パスワードは空にできません"}), 400
    
    # ユーザーIDの形式チェック（英数字のみ）
    if not re.match(r'^[a-zA-Z0-9]+$', username):
        return jsonify({"error": "ユーザーIDは英数字のみ使用可能です"}), 400
    
    # 既存ユーザーチェック
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        return jsonify({"error": "そのユーザーIDは既に存在します"}), 400
    
    try:
        # 新規ユーザー作成
        hashed_password = generate_password_hash(password)
        new_user = User(
            username=username,
            password_hash=hashed_password,
            name=name
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"ユーザー「{username}」（{name}）を作成しました",
            "user": {
                "id": new_user.id,
                "username": new_user.username,
                "name": new_user.name
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"ユーザー作成に失敗しました: {str(e)}"}), 500

@user_management_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
def delete_user(user_id):
    """ユーザー削除（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404
    
    # 自分自身を削除しようとした場合はエラー
    if user.username == current_user.username:
        return jsonify({"error": "自分自身を削除することはできません"}), 400
    
    try:
        username = user.username
        name = user.name or "unknown"
        
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"ユーザー「{username}」（{name}）を削除しました"
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"ユーザー削除に失敗しました: {str(e)}"}), 500

@user_management_bp.route("/api/users/<int:user_id>/password", methods=["PUT"])
@login_required
def change_password(user_id):
    """パスワード変更（管理者のみ）"""
    if not is_admin():
        return jsonify({"error": "管理者権限が必要です"}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "ユーザーが見つかりません"}), 404
    
    data = request.json
    new_password = data.get('password', '').strip()
    
    if not new_password:
        return jsonify({"error": "パスワードは空にできません"}), 400
    
    try:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"ユーザー「{user.username}」のパスワードを変更しました"
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"パスワード変更に失敗しました: {str(e)}"}), 500