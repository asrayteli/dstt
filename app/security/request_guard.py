"""ミューテーション系エンドポイントに対する軽量 CSRF 対策。

方針
----
社内ツールかつ同一オリジンの UI からのみ呼ばれるエンドポイントに対して、
``Origin`` と ``Referer`` ヘッダを確認する:

- 両方とも未設定な場合は通過させる（非ブラウザ/同一サーバのバックグラウンド呼出し互換）。
- ``Origin`` が設定されているなら、ホストが一致することを要求する。
- ``Origin`` が無く ``Referer`` だけあるなら、そのホストが一致することを要求する。

これにより、ログイン済みブラウザセッションを使った他サイトからの
CSRF 呼出しを防ぎつつ、同一オリジンの正規呼出し/既存のサーバ間連携は壊さない。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import abort, current_app, request


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _extract_netloc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return parts.netloc or None


def _request_host() -> str | None:
    host = request.host
    if host:
        return host
    return _extract_netloc(request.host_url)


def _is_origin_allowed() -> bool:
    method = (request.method or "").upper()
    if method in SAFE_METHODS:
        return True

    origin_host = _extract_netloc(request.headers.get("Origin"))
    referer_host = _extract_netloc(request.headers.get("Referer"))
    expected = _request_host()

    if origin_host is not None:
        return origin_host == expected
    if referer_host is not None:
        return referer_host == expected
    # 両方未設定: 同一サーバ上のスクリプトからの呼出しなど、従来の正規利用を壊さない
    return True


def enforce_same_origin_for_mutations() -> None:
    """``before_request`` フックから呼び出す。不一致なら 403 を返す。"""
    if _is_origin_allowed():
        return None
    current_app.logger.warning(
        "Blocked cross-origin %s to %s (origin=%r, referer=%r)",
        request.method,
        request.path,
        request.headers.get("Origin"),
        request.headers.get("Referer"),
    )
    abort(403)
