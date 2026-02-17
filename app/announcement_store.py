import json
import os
from datetime import datetime

from flask import current_app


def _store_path():
    os.makedirs(current_app.instance_path, exist_ok=True)
    return os.path.join(current_app.instance_path, "announcements.json")


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
    path = _store_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_announcements():
    data = _load_data()
    return sorted(
        data["announcements"],
        key=lambda x: x.get("id", 0),
        reverse=True,
    )


def create_announcement(title, content, created_by):
    data = _load_data()
    item = {
        "id": data["next_id"],
        "title": title or "お知らせ",
        "content": content,
        "created_by": created_by,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    data["announcements"].append(item)
    data["next_id"] += 1
    _save_data(data)
    return item


def delete_announcement(announcement_id):
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


def get_unread_announcements_for_user(username):
    data = _load_data()
    read_ids = set(data["reads"].get(username, []))
    result = []
    for a in data["announcements"]:
        aid = a.get("id")
        if aid in read_ids:
            continue
        if a.get("created_by") == username:
            continue
        result.append(a)
    result.sort(key=lambda x: x.get("id", 0), reverse=True)
    return result


def mark_announcements_read(username, announcement_ids):
    if not announcement_ids:
        return
    data = _load_data()
    read_ids = set(data["reads"].get(username, []))
    read_ids.update(announcement_ids)
    data["reads"][username] = sorted(read_ids)
    _save_data(data)
