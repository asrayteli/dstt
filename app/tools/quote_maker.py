"""見積書作成ツール（Quote Maker）。

あらゆる箇所を視覚的に編集でき、ブロックを自由に組み替えられる見積書を
作成・保存・PDF出力（ブラウザ印刷）するツール。作成した見積書は
ユーザーごとにサーバー保存し、一覧から再編集・複製・削除できる。

保存データ（``QuoteDocument.document``）はページ設定とブロック配列を
まとめた JSON で、レイアウト・文字・罫線・色などの編集内容を一括で保持する。
"""

from __future__ import annotations

import copy
import json

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from ..access_control import is_admin_user
from ..models import QuoteDocument, QuoteTemplate, db

quote_maker_bp = Blueprint(
    "quote_maker", __name__, url_prefix="/tools/quote_maker"
)

# document JSON の肥大化・DoS を避けるための保存サイズ上限（文字数）。
# 画像はデータURLで持つため、ロゴ・印影を数点埋め込んでも収まる余裕を取る。
_MAX_DOCUMENT_CHARS = 6 * 1024 * 1024
_MAX_TITLE_CHARS = 200
_MAX_TEMPLATE_NAME_CHARS = 200


def _current_username() -> str:
    return str(getattr(current_user, "username", "") or "")


def _clean_title(raw) -> str:
    title = (str(raw or "")).strip()
    if not title:
        title = "無題の見積書"
    return title[:_MAX_TITLE_CHARS]


def _validate_document(raw):
    """保存前の document を検証し、正規化した dict を返す。

    - dict であること
    - blocks が配列であること
    - JSON シリアライズ可能かつサイズ上限内であること
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("document はオブジェクトである必要があります。")

    blocks = raw.get("blocks", [])
    if not isinstance(blocks, list):
        raise ValueError("document.blocks は配列である必要があります。")

    page = raw.get("page", {})
    if page is not None and not isinstance(page, dict):
        raise ValueError("document.page はオブジェクトである必要があります。")

    try:
        serialized = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError("document をJSONに変換できませんでした。")
    if len(serialized) > _MAX_DOCUMENT_CHARS:
        raise ValueError("見積書のデータサイズが上限を超えています。")

    # 参照共有による副作用を避けるため深いコピーを返す。
    return copy.deepcopy(raw)


def _get_owned_quote_or_404(qid: int) -> QuoteDocument:
    quote = db.session.get(QuoteDocument, qid)
    if quote is None or quote.owner_user_id != _current_username():
        abort(404)
    return quote


@quote_maker_bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template(
        "quote_maker.html",
        page_title="DSTT - 見積書作成ツール",
        is_admin=is_admin_user(),
    )


@quote_maker_bp.route("/api/quotes", methods=["GET"])
@login_required
def list_quotes():
    rows = (
        QuoteDocument.query.filter_by(owner_user_id=_current_username())
        .order_by(QuoteDocument.updated_at.desc())
        .all()
    )
    return jsonify({"quotes": [row.to_summary() for row in rows]})


@quote_maker_bp.route("/api/quotes", methods=["POST"])
@login_required
def create_quote():
    payload = request.get_json(silent=True) or {}
    try:
        document = _validate_document(payload.get("document"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    quote = QuoteDocument(
        owner_user_id=_current_username(),
        title=_clean_title(payload.get("title")),
        document=document,
    )
    db.session.add(quote)
    db.session.commit()
    return jsonify(quote.to_detail()), 201


@quote_maker_bp.route("/api/quotes/<int:qid>", methods=["GET"])
@login_required
def get_quote(qid: int):
    quote = _get_owned_quote_or_404(qid)
    return jsonify(quote.to_detail())


@quote_maker_bp.route("/api/quotes/<int:qid>", methods=["PUT"])
@login_required
def update_quote(qid: int):
    quote = _get_owned_quote_or_404(qid)
    payload = request.get_json(silent=True) or {}

    if "document" in payload:
        try:
            quote.document = _validate_document(payload.get("document"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "title" in payload:
        quote.title = _clean_title(payload.get("title"))

    db.session.commit()
    return jsonify(quote.to_detail())


@quote_maker_bp.route("/api/quotes/<int:qid>/duplicate", methods=["POST"])
@login_required
def duplicate_quote(qid: int):
    quote = _get_owned_quote_or_404(qid)
    copy_title = f"{quote.title}（コピー）"[:_MAX_TITLE_CHARS]
    clone = QuoteDocument(
        owner_user_id=_current_username(),
        title=copy_title,
        document=copy.deepcopy(quote.document or {}),
    )
    db.session.add(clone)
    db.session.commit()
    return jsonify(clone.to_detail()), 201


@quote_maker_bp.route("/api/quotes/<int:qid>", methods=["DELETE"])
@login_required
def delete_quote(qid: int):
    quote = _get_owned_quote_or_404(qid)
    db.session.delete(quote)
    db.session.commit()
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# 基本テンプレート（会社共通）: 閲覧・利用は全ユーザー、追加/更新/削除は管理者のみ
# ---------------------------------------------------------------------------

def _clean_template_name(raw) -> str:
    name = (str(raw or "")).strip()
    if not name:
        name = "無題のテンプレート"
    return name[:_MAX_TEMPLATE_NAME_CHARS]


def _require_admin():
    """管理者でなければ 403 を返す（書き込み系テンプレート操作の共通ガード）。"""
    if not is_admin_user():
        abort(403, description="基本テンプレートの管理は管理者のみ可能です。")


@quote_maker_bp.route("/api/templates", methods=["GET"])
@login_required
def list_templates():
    rows = (
        QuoteTemplate.query.order_by(QuoteTemplate.updated_at.desc()).all()
    )
    return jsonify(
        {
            "templates": [row.to_summary() for row in rows],
            "can_manage": is_admin_user(),
        }
    )


@quote_maker_bp.route("/api/templates/<int:tid>", methods=["GET"])
@login_required
def get_template(tid: int):
    tpl = db.session.get(QuoteTemplate, tid)
    if tpl is None:
        abort(404)
    return jsonify(tpl.to_detail())


@quote_maker_bp.route("/api/templates", methods=["POST"])
@login_required
def create_template():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    try:
        document = _validate_document(payload.get("document"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    tpl = QuoteTemplate(
        name=_clean_template_name(payload.get("name")),
        document=document,
        created_by=_current_username(),
    )
    db.session.add(tpl)
    db.session.commit()
    return jsonify(tpl.to_detail()), 201


@quote_maker_bp.route("/api/templates/<int:tid>", methods=["PUT"])
@login_required
def update_template(tid: int):
    _require_admin()
    tpl = db.session.get(QuoteTemplate, tid)
    if tpl is None:
        abort(404)
    payload = request.get_json(silent=True) or {}
    if "document" in payload:
        try:
            tpl.document = _validate_document(payload.get("document"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "name" in payload:
        tpl.name = _clean_template_name(payload.get("name"))
    db.session.commit()
    return jsonify(tpl.to_detail())


@quote_maker_bp.route("/api/templates/<int:tid>", methods=["DELETE"])
@login_required
def delete_template(tid: int):
    _require_admin()
    tpl = db.session.get(QuoteTemplate, tid)
    if tpl is None:
        abort(404)
    db.session.delete(tpl)
    db.session.commit()
    return jsonify({"status": "ok"})
