import json
import os
from contextlib import contextmanager
from datetime import datetime

from flask import current_app

from .utils.atomic_io import write_json_atomic

try:
    import fcntl
except ImportError:  # Windows開発環境ではプロセス間ロックなしで動かす
    fcntl = None


def _store_path():
    os.makedirs(current_app.instance_path, exist_ok=True)
    return os.path.join(current_app.instance_path, "announcements.json")


@contextmanager
def _store_lock():
    """read-modify-write をプロセス間で直列化するロック。

    gunicorn のマルチワーカーでは、既読更新などの読み書きが並行すると
    後勝ちで他ワーカーの更新が失われる。flock で更新系操作を直列化する。
    """
    if fcntl is None:
        yield
        return
    lock_path = _store_path() + ".lock"
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _default_data():
    return {
        "next_id": 1,
        "announcements": [],
        "reads": {},
    }


def _load_data():
    path = _store_path()
    if not os.path.exists(path):
        return _default_data()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_data()
        data.setdefault("next_id", 1)
        data.setdefault("announcements", [])
        data.setdefault("reads", {})
        return data
    except Exception:
        return _default_data()


def _save_data(data):
    # 直接 open(path, "w") で書くと書き込み途中のクラッシュでファイルが
    # 破損する（お知らせ・既読情報の全損）。一時ファイル + os.replace で
    # 常に一貫した状態を保つ。
    write_json_atomic(_store_path(), data)


def is_announcement_expired(item, today=None):
    """掲載期限（expires_at, YYYY-MM-DD）を過ぎているかを返す。期限なしは False。"""
    raw = str(item.get("expires_at") or "").strip()
    if not raw:
        return False
    try:
        expires = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (today or datetime.now().date()) > expires


def announcement_matches_user(item, branch_id=None, office_ids=None):
    """対象指定（支店/営業所）にユーザーが該当するかを返す。指定なしは全員対象。"""
    target_branches = item.get("target_branch_ids") or []
    target_offices = item.get("target_office_ids") or []
    if not target_branches and not target_offices:
        return True
    if branch_id and int(branch_id) in {int(b) for b in target_branches}:
        return True
    offices = {int(o) for o in (office_ids or []) if o}
    if offices and offices & {int(o) for o in target_offices}:
        return True
    return False


def list_announcements():
    data = _load_data()
    return sorted(
        data["announcements"],
        key=lambda x: x.get("id", 0),
        reverse=True,
    )


def get_read_usernames(announcement_id):
    """指定お知らせを既読にしたユーザー名の集合を返す。"""
    data = _load_data()
    readers = set()
    for username, read_ids in data.get("reads", {}).items():
        if isinstance(read_ids, list) and announcement_id in read_ids:
            readers.add(username)
    return readers


def create_announcement(title, content, created_by,
                        expires_at=None, target_branch_ids=None, target_office_ids=None):
    with _store_lock():
        data = _load_data()
        item = {
            "id": data["next_id"],
            "title": title or "お知らせ",
            "content": content,
            "created_by": created_by,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "expires_at": expires_at or None,
            "target_branch_ids": [int(b) for b in (target_branch_ids or [])],
            "target_office_ids": [int(o) for o in (target_office_ids or [])],
        }
        data["announcements"].append(item)
        data["next_id"] += 1
        _save_data(data)
        return item


def delete_announcement(announcement_id):
    with _store_lock():
        data = _load_data()
        before = len(data["announcements"])
        data["announcements"] = [
            a for a in data["announcements"] if a.get("id") != announcement_id
        ]
        if len(data["announcements"]) == before:
            return False

        for username, read_ids in data["reads"].items():
            if isinstance(read_ids, list):
                data["reads"][username] = [rid for rid in read_ids if rid != announcement_id]

        _save_data(data)
        return True


def get_unread_announcements_for_user(username, branch_id=None, office_ids=None):
    data = _load_data()
    read_ids = set(data["reads"].get(username, []))
    today = datetime.now().date()
    result = []
    for a in data["announcements"]:
        aid = a.get("id")
        if aid in read_ids:
            continue
        if a.get("created_by") == username:
            continue
        if is_announcement_expired(a, today):
            continue
        if not announcement_matches_user(a, branch_id=branch_id, office_ids=office_ids):
            continue
        result.append(a)
    result.sort(key=lambda x: x.get("id", 0), reverse=True)
    return result


def mark_announcements_read(username, announcement_ids):
    if not announcement_ids:
        return
    with _store_lock():
        data = _load_data()
        read_ids = set(data["reads"].get(username, []))
        read_ids.update(announcement_ids)
        data["reads"][username] = sorted(read_ids)
        _save_data(data)
