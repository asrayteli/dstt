"""ミューテーション系リクエスト全般に対する軽量 CSRF 対策。

方針
----
アプリ全体の状態変更系リクエスト（GET/HEAD/OPTIONS/TRACE 以外）に対して、
``Origin`` と ``Referer`` ヘッダを確認する:

- 両方とも未設定な場合は通過させる（非ブラウザ/同一サーバのバックグラウンド呼出し互換）。
- ``Origin`` が設定されているなら、ホストが一致することを要求する。
- ``Origin`` が無く ``Referer`` だけあるなら、そのホストが一致することを要求する。

これにより、ログイン済みブラウザセッションを使った他サイトからの
CSRF 呼出しを防ぎつつ、同一オリジンの正規呼出し/既存のサーバ間連携は壊さない。

「両方未設定なら通過」は防御を弱めない: 現代ブラウザは POST/PUT/DELETE 等の
不安全メソッドで必ず ``Origin`` を送るため、ブラウザ起因の CSRF は ``Origin``
不一致として確実にブロックされる。``Origin`` を持たないのは curl 等の
非ブラウザ/サーバ間呼出しのみで、これらは CSRF の攻撃ベクタにならない。
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

    # Fetch Metadata cannot be set by page JavaScript.  Check it before the
    # legacy Origin/Referer fallback so privacy software stripping those two
    # headers cannot turn an explicitly cross-site browser request into an
    # allowed headerless request.
    fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site == "cross-site":
        return False

    origin_raw = request.headers.get("Origin")
    # sandboxed iframe や data: URL 由来のリクエストは "Origin: null" を送る。
    # netloc が取れず「未設定」と同じ扱いになると素通りしてしまうため、
    # 明示的にクロスオリジンとして拒否する。
    if origin_raw and origin_raw.strip().lower() == "null":
        return False

    origin_host = _extract_netloc(origin_raw)
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
