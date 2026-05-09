from __future__ import annotations

import base64
import csv
import html
import io
import re
import secrets
import shutil
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    has_request_context,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import current_user, login_required

from app.models import PowerVoteForm, PowerVoteQuestion, PowerVoteResponse, User, db


powervote_bp = Blueprint("powervote", __name__)

FORM_STATUSES = {"draft", "open", "closed"}
IDENTITY_MODES = {"anonymous", "optional", "required"}
RESULT_VISIBILITIES = {"creator_only"}
QUESTION_TYPES = {
    "short_text",
    "long_text",
    "single_choice",
    "multi_choice",
    "number",
    "rating",
    "date",
    "time",
    "email",
    "phone",
    "url",
    "consent",
    "info",
    "section",
    "partition",
}
COOKIE_PREFIX = "powervote_answered_"
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_DESCRIPTION_HTML_LENGTH = 20000
MAX_OPTIONS = 200
MAX_SHARED_EDITORS = 100
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
STYLE_COLOR_RE = re.compile(r"(?:^|;)\s*color\s*:\s*([^;]+)\s*(?:;|$)", re.IGNORECASE)
STYLE_ALIGN_RE = re.compile(r"(?:^|;)\s*text-align\s*:\s*(left|center|right)\s*(?:;|$)", re.IGNORECASE)
STYLE_WIDTH_RE = re.compile(r"(?:^|;)\s*width\s*:\s*(\d{1,3})%\s*(?:;|$)", re.IGNORECASE)
RGB_COLOR_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)", re.IGNORECASE)
UPLOAD_SRC_RE = re.compile(r"^/tools/powervote/uploads/\d+/[A-Za-z0-9_.-]+$")
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _current_username() -> str:
    if has_request_context():
        session_user_id = session.get("_user_id")
        if session_user_id:
            return str(session_user_id)
    return str(getattr(current_user, "username", "") or "")


def _new_public_token() -> str:
    for _ in range(20):
        token = secrets.token_urlsafe(18)
        if PowerVoteForm.query.filter_by(public_token=token).first() is None:
            return token
    raise RuntimeError("Could not generate unique PowerVote token")


def _normalize_css_color(value: Any) -> str:
    color = str(value or "").strip()
    if HEX_COLOR_RE.match(color):
        return color.lower()
    match = RGB_COLOR_RE.fullmatch(color)
    if not match:
        return ""
    channels = [max(0, min(255, int(part))) for part in match.groups()]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _safe_image_width(attrs: dict[str, str]) -> int:
    raw_width = attrs.get("data-width", "")
    if not raw_width:
        match = STYLE_WIDTH_RE.search(attrs.get("style", ""))
        raw_width = match.group(1) if match else ""
    return _safe_int(raw_width, 100, min_value=10, max_value=100)


def _safe_image_align(attrs: dict[str, str]) -> str:
    align = attrs.get("data-align", "").lower()
    if align in {"left", "center", "right"}:
        return align
    style = attrs.get("style", "").lower()
    has_left_auto = "margin-left: auto" in style or "margin-left:auto" in style
    has_right_auto = "margin-right: auto" in style or "margin-right:auto" in style
    if has_left_auto and has_right_auto:
        return "center"
    if has_left_auto:
        return "right"
    return "left"


def _image_style(width: int, align: str) -> str:
    margins = {
        "center": "margin-left: auto; margin-right: auto;",
        "right": "margin-left: auto; margin-right: 0;",
        "left": "margin-left: 0; margin-right: auto;",
    }[align]
    return f"width: {width}%; height: auto; display: block; {margins}"


def _safe_str(value: Any, max_len: int, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()[:max_len]


def _safe_text(value: Any, max_len: int, default: str = "") -> str:
    if value is None:
        return default
    return str(value)[:max_len]


def _safe_json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class _DescriptionHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in {"strong", "b"}:
            self.parts.append("<strong>")
            self.stack.append("strong")
        elif tag == "u":
            self.parts.append("<u>")
            self.stack.append("u")
        elif tag in {"span", "font"}:
            color = ""
            style_match = STYLE_COLOR_RE.search(attrs_dict.get("style", ""))
            if style_match:
                color = _normalize_css_color(style_match.group(1))
            elif attrs_dict.get("color"):
                color = _normalize_css_color(attrs_dict["color"])
            if color:
                self.parts.append(f'<span style="color: {color}">')
                self.stack.append("span")
        elif tag in {"div", "p"}:
            align = ""
            align_match = STYLE_ALIGN_RE.search(attrs_dict.get("style", ""))
            if align_match:
                align = align_match.group(1).lower()
            elif attrs_dict.get("align", "").lower() in {"left", "center", "right"}:
                align = attrs_dict["align"].lower()
            if self.parts:
                self.parts.append("<br>")
            if align:
                self.parts.append(f'<div style="text-align: {align}">')
                self.stack.append("div")
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href.startswith(("http://", "https://", "mailto:")):
                self.parts.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
                self.stack.append("a")
        elif tag == "img":
            src = attrs_dict.get("src", "").strip()
            alt = _safe_str(attrs_dict.get("alt"), 200)
            if UPLOAD_SRC_RE.match(src):
                width = _safe_image_width(attrs_dict)
                align = _safe_image_align(attrs_dict)
                style = _image_style(width, align)
                self.parts.append(
                    f'<img src="{html.escape(src, quote=True)}" '
                    f'alt="{html.escape(alt, quote=True)}" '
                    f'data-width="{width}" data-align="{align}" '
                    f'style="{html.escape(style, quote=True)}">'
                )
        elif tag == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        tag = "strong" if tag.lower() == "b" else "span" if tag.lower() == "font" else tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag not in {"strong", "u", "span", "div", "a"}:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                while len(self.stack) > index:
                    closing = self.stack.pop()
                    self.parts.append(f"</{closing}>")
                break

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.parts.append(html.escape(data, quote=False))

    def close_open_tags(self) -> None:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")


def _sanitize_description_html(value: Any) -> str:
    if not value:
        return ""
    parser = _DescriptionHTMLSanitizer()
    parser.feed(str(value)[:MAX_DESCRIPTION_HTML_LENGTH])
    parser.close_open_tags()
    return "".join(parser.parts)[:MAX_DESCRIPTION_HTML_LENGTH]


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _safe_int(value: Any, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if min_value is not None:
        number = max(min_value, number)
    if max_value is not None:
        number = min(max_value, number)
    return number


def _safe_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_username_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[\s,、]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    usernames: list[str] = []
    seen: set[str] = set()
    for raw in raw_items[:MAX_SHARED_EDITORS]:
        username = _safe_str(raw, 80)
        if username and username not in seen:
            seen.add(username)
            usernames.append(username)
    return usernames


def _shared_editors(form: PowerVoteForm) -> list[str]:
    settings = form.settings or {}
    return _safe_username_list(settings.get("shared_editors"))


def _shared_editor_details(form: PowerVoteForm) -> list[dict[str, str]]:
    usernames = _shared_editors(form)
    if not usernames:
        return []
    users = {
        user.username: user
        for user in User.query.filter(User.username.in_(usernames)).all()
    }
    return [
        {"username": username, "name": users[username].name or username}
        for username in usernames
        if username in users
    ]


def _user_can_edit_form(form: PowerVoteForm) -> bool:
    username = _current_username()
    return form.owner_user_id == username or username in _shared_editors(form)


def _normalize_shared_editors(value: Any, owner_username: str) -> list[str]:
    usernames = [username for username in _safe_username_list(value) if username != owner_username]
    if not usernames:
        return []
    existing = {
        user.username
        for user in User.query.filter(User.username.in_(usernames)).all()
    }
    missing = [username for username in usernames if username not in existing]
    if missing:
        raise ValueError(f"共有先ユーザーが見つかりません: {', '.join(missing)}")
    return usernames


def _sanitize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    sanitized = settings.copy()
    if "description_html" in sanitized:
        sanitized["description_html"] = _sanitize_description_html(sanitized.get("description_html"))
    return sanitized


def _sanitize_theme(theme: Any) -> dict[str, Any]:
    raw = _safe_json_object(theme)
    sanitized = raw.copy()
    sanitized["accent"] = _normalize_css_color(raw.get("accent")) or "#2563eb"
    sanitized["base_text"] = _normalize_css_color(raw.get("base_text")) or "#0f172a"
    return sanitized


def _owner_display_name(username: str) -> str:
    user = User.query.filter_by(username=username).first()
    return user.name if user and user.name else username


def _upload_root() -> Path:
    return Path(current_app.instance_path) / "powervote_uploads"


def _form_upload_dir(form_id: int) -> Path:
    return _upload_root() / f"form_{form_id}"


def _delete_form_uploads(form_id: int) -> None:
    shutil.rmtree(_form_upload_dir(form_id), ignore_errors=True)


def _normalize_question_type(question_type: str) -> str:
    return "info" if question_type == "section" else question_type


def _is_answerless_type(question_type: str) -> bool:
    return _normalize_question_type(question_type) in {"info", "partition"}


def _safe_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value[:MAX_OPTIONS]:
        if isinstance(raw, dict):
            label = _safe_str(raw.get("label"), 240)
            option_id = _safe_str(raw.get("id"), 80)
        else:
            label = _safe_str(raw, 240)
            option_id = ""
        if not label:
            continue
        if not option_id:
            option_id = secrets.token_urlsafe(6)
        if option_id in seen:
            base_id = option_id
            suffix = 2
            while option_id in seen:
                option_id = f"{base_id}-{suffix}"
                suffix += 1
        seen.add(option_id)
        options.append({"id": option_id, "label": label})
    return options


def _serialize_question(question: PowerVoteQuestion) -> dict[str, Any]:
    question_type = _normalize_question_type(question.question_type)
    return {
        "id": question.id,
        "type": question_type,
        "title": question.title,
        "description": question.description,
        "required": question.is_required,
        "sort_order": question.sort_order,
        "options": question.options or [],
        "settings": question.settings or {},
    }


def _serialize_form(form: PowerVoteForm, *, include_questions: bool = True) -> dict[str, Any]:
    data = {
        "id": form.id,
        "owner_user_id": form.owner_user_id,
        "owner_display_name": _owner_display_name(form.owner_user_id),
        "is_shared_to_me": _current_username() in _shared_editors(form),
        "title": form.title,
        "description": form.description,
        "status": form.status,
        "public_token": form.public_token,
        "public_url": url_for("powervote.public_form", token=form.public_token, _external=True),
        "is_anonymous": form.is_anonymous,
        "identity_mode": form.identity_mode,
        "cookie_limit_enabled": form.cookie_limit_enabled,
        "result_visibility": form.result_visibility,
        "theme": form.theme or {},
        "settings": form.settings or {},
        "shared_editors": _shared_editors(form),
        "shared_editor_details": _shared_editor_details(form),
        "can_manage_shares": form.owner_user_id == _current_username(),
        "created_at": form.created_at.isoformat() if form.created_at else None,
        "updated_at": form.updated_at.isoformat() if form.updated_at else None,
        "question_count": len(form.questions),
        "response_count": len(form.responses),
    }
    if include_questions:
        data["questions"] = [_serialize_question(q) for q in form.questions]
    return data


def _serialize_public_form(form: PowerVoteForm, *, force_open: bool = False) -> dict[str, Any]:
    status = "open" if force_open else form.status
    questions = [_serialize_question(q) for q in form.questions] if status == "open" else []
    return {
        "title": form.title,
        "description": form.description,
        "status": status,
        "is_anonymous": form.is_anonymous,
        "identity_mode": form.identity_mode,
        "cookie_limit_enabled": form.cookie_limit_enabled,
        "theme": form.theme or {},
        "settings": form.settings or {},
        "questions": questions,
    }


def _owned_form_or_404(form_id: int) -> PowerVoteForm:
    form = db.session.get(PowerVoteForm, form_id)
    if form is None:
        abort(404)
    if form.owner_user_id != _current_username():
        abort(403)
    return form


def _editable_form_or_404(form_id: int) -> PowerVoteForm:
    form = db.session.get(PowerVoteForm, form_id)
    if form is None:
        abort(404)
    if not _user_can_edit_form(form):
        abort(403)
    return form


def _apply_form_payload(form: PowerVoteForm, payload: dict[str, Any]) -> None:
    if "title" in payload:
        title = _safe_str(payload.get("title"), MAX_TITLE_LENGTH)
        if not title:
            raise ValueError("タイトルを入力してください。")
        form.title = title
    if "description" in payload:
        form.description = _safe_text(payload.get("description"), MAX_DESCRIPTION_LENGTH)
    if "status" in payload:
        status = _safe_str(payload.get("status"), 20, "draft")
        if status not in FORM_STATUSES:
            raise ValueError("公開状態が不正です。")
        form.status = status
    if "is_anonymous" in payload:
        form.is_anonymous = bool(payload.get("is_anonymous"))
    if "identity_mode" in payload:
        identity_mode = _safe_str(payload.get("identity_mode"), 20, "anonymous")
        if identity_mode not in IDENTITY_MODES:
            raise ValueError("回答者情報の設定が不正です。")
        form.identity_mode = identity_mode
    if "cookie_limit_enabled" in payload:
        form.cookie_limit_enabled = bool(payload.get("cookie_limit_enabled"))
    if "result_visibility" in payload:
        visibility = _safe_str(payload.get("result_visibility"), 20, "creator_only")
        if visibility not in RESULT_VISIBILITIES:
            raise ValueError("結果公開設定が不正です。")
        form.result_visibility = visibility
    if "theme" in payload:
        form.theme = _sanitize_theme(payload.get("theme"))
    if "settings" in payload:
        next_settings = _sanitize_settings(_safe_json_object(payload.get("settings")))
        if form.owner_user_id == _current_username():
            next_settings["shared_editors"] = _normalize_shared_editors(
                next_settings.get("shared_editors"),
                form.owner_user_id,
            )
        else:
            next_settings["shared_editors"] = _shared_editors(form)
        form.settings = next_settings
    form.updated_at = datetime.utcnow()


def _apply_questions_payload(form: PowerVoteForm, raw_questions: Any) -> None:
    if raw_questions is None:
        return
    if not isinstance(raw_questions, list):
        raise ValueError("質問データが不正です。")
    existing = {question.id: question for question in form.questions}
    keep_ids: set[int] = set()

    for index, raw in enumerate(raw_questions):
        if not isinstance(raw, dict):
            raise ValueError("質問データが不正です。")
        question_type = _normalize_question_type(_safe_str(raw.get("type") or raw.get("question_type"), 40, "short_text"))
        if question_type not in QUESTION_TYPES:
            raise ValueError("未対応の質問タイプが含まれています。")

        raw_id = raw.get("id")
        question = None
        if raw_id:
            try:
                question = existing.get(int(raw_id))
            except (TypeError, ValueError):
                question = None
        if question is None:
            question = PowerVoteQuestion(form=form)
            db.session.add(question)

        title = _safe_str(raw.get("title"), 300)
        if not _is_answerless_type(question_type) and not title:
            raise ValueError("質問文を入力してください。")

        question.question_type = question_type
        question.title = title or ("次のページ" if question_type == "partition" else "見出し")
        question.description = _safe_text(raw.get("description"), MAX_DESCRIPTION_LENGTH)
        required_value = raw.get("required") if "required" in raw else raw.get("is_required")
        question.is_required = False if _is_answerless_type(question_type) else _safe_bool(required_value)
        question.sort_order = _safe_int(raw.get("sort_order"), index, min_value=0)
        question.options = _safe_options(raw.get("options"))
        question.settings = _sanitize_settings(_safe_json_object(raw.get("settings")))
        if question_type in {"single_choice", "multi_choice"} and not question.options:
            raise ValueError("選択式の質問には選択肢が必要です。")
        if question.id:
            keep_ids.add(question.id)

    for question in list(form.questions):
        if question.id and question.id not in keep_ids and question.id in existing:
            db.session.delete(question)


def _cookie_name(form: PowerVoteForm) -> str:
    token_part = form.public_token.replace("-", "_")
    return f"{COOKIE_PREFIX}{token_part}"


def _answer_value_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _option_ids(question: PowerVoteQuestion) -> set[str]:
    return {str(option.get("id")) for option in (question.options or []) if isinstance(option, dict)}


def _flow_key(question: PowerVoteQuestion) -> str:
    settings = question.settings or {}
    flow = settings.get("flow") if isinstance(settings.get("flow"), dict) else {}
    return _safe_str(flow.get("key"), 80)


def _branch_target_for_answer(question: PowerVoteQuestion, answers: dict[str, Any]) -> str:
    qtype = _normalize_question_type(question.question_type)
    settings = question.settings or {}
    default_next = _safe_str(settings.get("default_next"), 80)
    if qtype != "single_choice":
        return default_next
    branches = settings.get("branches") if isinstance(settings.get("branches"), dict) else {}
    answer = answers.get(str(question.id))
    if answer is None:
        return default_next
    return _safe_str(branches.get(str(answer)) or default_next, 80)


def _reachable_questions(form: PowerVoteForm, answers: dict[str, Any]) -> list[PowerVoteQuestion]:
    questions = list(form.questions)
    if not questions:
        return []
    settings = form.settings or {}
    manual_flow = settings.get("flow_mode") == "manual"
    index_by_id: dict[str, int] = {}
    for index, question in enumerate(questions):
        if question.id is not None:
            index_by_id[str(question.id)] = index
        key = _flow_key(question)
        if key:
            index_by_id[key] = index
    visible: list[PowerVoteQuestion] = []
    seen: set[int] = set()
    start_target = _safe_str(settings.get("flow_start"), 80)
    if manual_flow and not start_target:
        return []
    if start_target == "__end__":
        return []
    index = index_by_id.get(start_target, 0)

    while 0 <= index < len(questions) and index not in seen:
        seen.add(index)
        question = questions[index]
        visible.append(question)

        target = _branch_target_for_answer(question, answers)
        if target == "__end__":
            break
        if target and target in index_by_id:
            index = index_by_id[target]
        elif manual_flow:
            break
        else:
            index += 1

    return visible


def _validate_answer(question: PowerVoteQuestion, raw: Any) -> Any:
    qtype = question.question_type
    settings = question.settings or {}

    qtype = _normalize_question_type(qtype)
    if _is_answerless_type(qtype):
        return None
    if question.is_required and _answer_value_is_empty(raw):
        raise ValueError(f"「{question.title}」は必須です。")
    if _answer_value_is_empty(raw):
        return "" if qtype not in {"multi_choice", "consent"} else ([] if qtype == "multi_choice" else False)

    if qtype in {"short_text", "long_text", "phone"}:
        max_length = _safe_int(settings.get("max_length"), 5000, min_value=1, max_value=5000)
        min_length = _safe_int(settings.get("min_length"), 0, min_value=0, max_value=max_length)
        value = _safe_str(raw, max_length)
        if min_length and len(value) < min_length:
            raise ValueError(f"「{question.title}」は{min_length}文字以上で回答してください。")
        return value
    if qtype == "email":
        max_length = _safe_int(settings.get("max_length"), 240, min_value=1, max_value=240)
        min_length = _safe_int(settings.get("min_length"), 0, min_value=0, max_value=max_length)
        value = _safe_str(raw, max_length)
        if min_length and len(value) < min_length:
            raise ValueError(f"「{question.title}」は{min_length}文字以上で回答してください。")
        if value and not EMAIL_RE.match(value):
            raise ValueError(f"「{question.title}」はメールアドレスで回答してください。")
        return value
    if qtype == "url":
        max_length = _safe_int(settings.get("max_length"), 500, min_value=1, max_value=500)
        min_length = _safe_int(settings.get("min_length"), 0, min_value=0, max_value=max_length)
        value = _safe_str(raw, max_length)
        if min_length and len(value) < min_length:
            raise ValueError(f"「{question.title}」は{min_length}文字以上で回答してください。")
        if value and not URL_RE.match(value):
            raise ValueError(f"「{question.title}」は https:// から始まるURLで回答してください。")
        return value
    if qtype == "single_choice":
        value = _safe_str(raw, 120)
        if value not in _option_ids(question):
            raise ValueError(f"「{question.title}」の選択肢が不正です。")
        return value
    if qtype == "multi_choice":
        if not isinstance(raw, list):
            raise ValueError(f"「{question.title}」は複数選択で回答してください。")
        ids = _option_ids(question)
        values = [_safe_str(item, 120) for item in raw]
        if any(value not in ids for value in values):
            raise ValueError(f"「{question.title}」の選択肢が不正です。")
        return values
    if qtype == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"「{question.title}」は数値で回答してください。") from exc
        min_setting = _safe_float(settings.get("min"))
        max_setting = _safe_float(settings.get("max"))
        if min_setting is not None and number < min_setting:
            raise ValueError(f"「{question.title}」が下限を下回っています。")
        if max_setting is not None and number > max_setting:
            raise ValueError(f"「{question.title}」が上限を超えています。")
        if _safe_bool(settings.get("integer_only")) and not number.is_integer():
            raise ValueError(f"「{question.title}」は整数で回答してください。")
        return number
    if qtype == "rating":
        try:
            rating = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"「{question.title}」は評価値で回答してください。") from exc
        min_value = _safe_int(settings.get("min"), 1, min_value=0, max_value=100)
        max_value = _safe_int(settings.get("max"), 5, min_value=0, max_value=100)
        if max_value < min_value:
            max_value = min_value
        if rating < min_value or rating > max_value:
            raise ValueError(f"「{question.title}」の評価値が範囲外です。")
        return rating
    if qtype in {"date", "time"}:
        return _safe_str(raw, 40)
    if qtype == "consent":
        accepted = bool(raw)
        if question.is_required and not accepted:
            raise ValueError(f"「{question.title}」に同意してください。")
        return accepted
    return raw


def _validate_response_payload(form: PowerVoteForm, payload: dict[str, Any]) -> dict[str, Any]:
    respondent = payload.get("respondent") if isinstance(payload.get("respondent"), dict) else {}
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    name = _safe_str(respondent.get("name"), 200)
    email = _safe_str(respondent.get("email"), 200)

    if not form.is_anonymous and form.identity_mode == "required" and not name:
        raise ValueError("お名前を入力してください。")
    if form.is_anonymous:
        name = ""
        email = ""

    clean_answers: dict[str, Any] = {}
    for question in _reachable_questions(form, answers):
        qid = str(question.id)
        value = _validate_answer(question, answers.get(qid))
        if not _is_answerless_type(question.question_type):
            clean_answers[qid] = value
    return {"respondent_name": name, "respondent_email": email, "answers": clean_answers}


def _option_label(question: PowerVoteQuestion, option_id: str) -> str:
    for option in question.options or []:
        if isinstance(option, dict) and str(option.get("id")) == str(option_id):
            return str(option.get("label") or option_id)
    return str(option_id)


def _summarize_results(form: PowerVoteForm) -> list[dict[str, Any]]:
    summaries = []
    for question in form.questions:
        qtype = _normalize_question_type(question.question_type)
        if _is_answerless_type(qtype):
            continue
        counts: dict[str, int] = {}
        texts: list[str] = []
        numbers: list[float] = []
        for response in form.responses:
            value = (response.answers or {}).get(str(question.id))
            if _answer_value_is_empty(value):
                continue
            if qtype in {"single_choice", "rating", "consent"}:
                key = _option_label(question, str(value)) if qtype == "single_choice" else str(value)
                counts[key] = counts.get(key, 0) + 1
            elif qtype == "multi_choice" and isinstance(value, list):
                for item in value:
                    key = _option_label(question, str(item))
                    counts[key] = counts.get(key, 0) + 1
            elif qtype == "number":
                try:
                    numbers.append(float(value))
                except (TypeError, ValueError):
                    pass
            else:
                texts.append(str(value))
        summary: dict[str, Any] = {
            "question_id": question.id,
            "title": question.title,
            "type": qtype,
            "counts": counts,
            "texts": texts[:50],
        }
        if numbers:
            summary["number"] = {
                "count": len(numbers),
                "min": min(numbers),
                "max": max(numbers),
                "average": sum(numbers) / len(numbers),
            }
        summaries.append(summary)
    return summaries


def _make_qr_data(url: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@powervote_bp.route("/tools/powervote/", methods=["GET"])
@login_required
def home():
    return render_template("powervote.html", page_title="DSTT - PowerVote")


@powervote_bp.route("/tools/powervote/preview/<int:form_id>", methods=["GET"])
@login_required
def preview_form(form_id: int):
    form = _editable_form_or_404(form_id)
    return render_template(
        "powervote_public.html",
        form_payload=_serialize_public_form(form, force_open=True),
        submit_url="#",
        answered=False,
        preview_mode=True,
        page_title=f"PowerVote Preview - {form.title}",
    )


@powervote_bp.route("/tools/powervote/flow/<int:form_id>", methods=["GET"])
@login_required
def flow_editor(form_id: int):
    form = _editable_form_or_404(form_id)
    return render_template(
        "powervote_flow.html",
        form_id=form.id,
        page_title=f"PowerVote Flow - {form.title}",
    )


@powervote_bp.route("/tools/powervote/api/forms", methods=["GET"])
@login_required
def api_forms():
    forms = [
        form
        for form in PowerVoteForm.query.order_by(PowerVoteForm.updated_at.desc()).all()
        if _user_can_edit_form(form)
    ]
    return jsonify({"ok": True, "forms": [_serialize_form(form, include_questions=False) for form in forms]})


@powervote_bp.route("/tools/powervote/api/users", methods=["GET"])
@login_required
def api_users():
    query = _safe_str(request.args.get("query"), 80)
    if not query:
        return jsonify({"ok": True, "users": []})
    users = (
        User.query.filter(User.name.ilike(f"%{query}%"))
        .order_by(User.name.asc(), User.username.asc())
        .limit(20)
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "users": [
                {"username": user.username, "name": user.name or user.username}
                for user in users
            ],
        }
    )


@powervote_bp.route("/tools/powervote/api/forms", methods=["POST"])
@login_required
def api_create_form():
    payload = request.get_json(silent=True) or {}
    title = _safe_str(payload.get("title"), MAX_TITLE_LENGTH, "新しいPowerVote")
    form = PowerVoteForm(
        owner_user_id=_current_username(),
        title=title or "新しいPowerVote",
        description="",
        public_token=_new_public_token(),
        theme={"accent": "#2563eb", "base_text": "#0f172a"},
        settings={},
    )
    db.session.add(form)
    db.session.commit()
    return jsonify({"ok": True, "form": _serialize_form(form)}), 201


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>", methods=["GET"])
@login_required
def api_get_form(form_id: int):
    return jsonify({"ok": True, "form": _serialize_form(_editable_form_or_404(form_id))})


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>", methods=["PUT"])
@login_required
def api_update_form(form_id: int):
    form = _editable_form_or_404(form_id)
    payload = request.get_json(silent=True) or {}
    try:
        _apply_form_payload(form, payload)
        _apply_questions_payload(form, payload.get("questions"))
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)
    return jsonify({"ok": True, "form": _serialize_form(form)})


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>", methods=["DELETE"])
@login_required
def api_delete_form(form_id: int):
    form = _owned_form_or_404(form_id)
    _delete_form_uploads(form.id)
    db.session.delete(form)
    db.session.commit()
    return jsonify({"ok": True})


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>/images", methods=["POST"])
@login_required
def api_upload_image(form_id: int):
    form = _editable_form_or_404(form_id)
    if request.content_length and request.content_length > MAX_IMAGE_UPLOAD_BYTES:
        return _json_error("画像サイズは5MBまでです。", 413)
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return _json_error("画像ファイルを選択してください。", 400)
    extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return _json_error("PNG/JPEG/GIF/WebP画像のみアップロードできます。", 400)

    upload_dir = _form_upload_dir(form.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_urlsafe(16)}.{extension}"
    upload.save(upload_dir / filename)
    return jsonify(
        {
            "ok": True,
            "url": url_for("powervote.uploaded_image", form_id=form.id, filename=filename),
        }
    )


@powervote_bp.route("/tools/powervote/uploads/<int:form_id>/<path:filename>", methods=["GET"])
def uploaded_image(form_id: int, filename: str):
    form = db.session.get(PowerVoteForm, form_id)
    if form is None:
        abort(404)
    safe_name = Path(filename).name
    if safe_name != filename:
        abort(404)
    return send_from_directory(_form_upload_dir(form_id), safe_name)


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>/regenerate-token", methods=["POST"])
@login_required
def api_regenerate_token(form_id: int):
    form = _owned_form_or_404(form_id)
    form.public_token = _new_public_token()
    form.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "form": _serialize_form(form)})


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>/qr", methods=["GET"])
@login_required
def api_form_qr(form_id: int):
    form = _editable_form_or_404(form_id)
    public_url = url_for("powervote.public_form", token=form.public_token, _external=True)
    return jsonify({"ok": True, "url": public_url, "qr_data": _make_qr_data(public_url)})


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>/results", methods=["GET"])
@login_required
def api_results(form_id: int):
    form = _editable_form_or_404(form_id)
    responses = [
        {
            "id": response.id,
            "respondent_name": response.respondent_name,
            "respondent_email": response.respondent_email,
            "submitted_at": response.submitted_at.isoformat() if response.submitted_at else None,
            "answers": response.answers or {},
        }
        for response in form.responses
    ]
    return jsonify(
        {
            "ok": True,
            "form": _serialize_form(form),
            "responses": responses,
            "summary": _summarize_results(form),
        }
    )


@powervote_bp.route("/tools/powervote/api/forms/<int:form_id>/export.csv", methods=["GET"])
@login_required
def api_export_csv(form_id: int):
    form = _editable_form_or_404(form_id)
    output = io.StringIO()
    writer = csv.writer(output)
    questions = [question for question in form.questions if not _is_answerless_type(question.question_type)]
    writer.writerow(["submitted_at", "respondent_name", "respondent_email", *[q.title for q in questions]])
    for response in reversed(form.responses):
        answers = response.answers or {}
        row = [
            response.submitted_at.isoformat() if response.submitted_at else "",
            response.respondent_name,
            response.respondent_email,
        ]
        for question in questions:
            value = answers.get(str(question.id), "")
            qtype = _normalize_question_type(question.question_type)
            if qtype == "single_choice":
                value = _option_label(question, str(value)) if value else ""
            elif qtype == "multi_choice" and isinstance(value, list):
                value = ", ".join(_option_label(question, str(item)) for item in value)
            row.append(value)
        writer.writerow(row)
    filename = f"powervote_{form.id}_responses.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@powervote_bp.route("/vote/<token>", methods=["GET"])
def public_form(token: str):
    form = PowerVoteForm.query.filter_by(public_token=token).first_or_404()
    answered = bool(request.cookies.get(_cookie_name(form)))
    return render_template(
        "powervote_public.html",
        form_payload=_serialize_public_form(form),
        submit_url=url_for("powervote.public_submit", token=token),
        answered=answered,
        page_title=f"PowerVote - {form.title}",
    )


@powervote_bp.route("/vote/<token>/submit", methods=["POST"])
def public_submit(token: str):
    form = PowerVoteForm.query.filter_by(public_token=token).first_or_404()
    if form.status != "open":
        return _json_error("このPowerVoteは現在回答を受け付けていません。", 403)

    cookie_name = _cookie_name(form)
    if form.cookie_limit_enabled and request.cookies.get(cookie_name):
        return _json_error("このブラウザではすでに回答済みです。", 409)

    payload = request.get_json(silent=True) or {}
    try:
        clean = _validate_response_payload(form, payload)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    cookie_id = secrets.token_urlsafe(24)
    response_row = PowerVoteResponse(
        form_id=form.id,
        respondent_name=clean["respondent_name"],
        respondent_email=clean["respondent_email"],
        respondent_cookie_id=cookie_id,
        answers=clean["answers"],
    )
    db.session.add(response_row)
    db.session.commit()

    response = make_response(jsonify({"ok": True, "message": "回答を送信しました。"}), 201)
    if form.cookie_limit_enabled:
        response.set_cookie(
            cookie_name,
            cookie_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="Lax",
        )
    return response
