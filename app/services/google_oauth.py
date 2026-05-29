"""Google OAuth2 (Web Server Flow) の薄いラッパ。

Google公式SDKは使わず、``requests`` で OAuth2 エンドポイントを直接叩く。
DSTT 既存の「依存を増やさず自前で薄く実装する」流儀に合わせている。

設定は環境変数から読む。
- ``DSTT_GOOGLE_CLIENT_ID``
- ``DSTT_GOOGLE_CLIENT_SECRET``

未設定なら ``is_configured()`` が False を返し、連携UIは表示しない。
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

# Calendar イベントの読み書きと、表示用メールアドレスの取得だけに絞る。
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
]

_HTTP_TIMEOUT = 15


class GoogleOAuthError(RuntimeError):
    """OAuth/トークン処理の失敗。"""


def client_id() -> str:
    return (os.environ.get("DSTT_GOOGLE_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("DSTT_GOOGLE_CLIENT_SECRET") or "").strip()


def is_configured() -> bool:
    """OAuthクライアント情報が環境変数で設定されているか。"""
    return bool(client_id() and _client_secret())


def build_auth_url(state: str, redirect_uri: str) -> str:
    """同意画面へのリダイレクトURLを組み立てる。

    refresh_token を確実に得るため ``access_type=offline`` と
    ``prompt=consent`` を付ける。
    """
    if not is_configured():
        raise GoogleOAuthError("Google連携が未設定です（環境変数 DSTT_GOOGLE_CLIENT_ID / SECRET）。")
    from urllib.parse import urlencode

    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """認可コードを access/refresh token に交換する。"""
    if not is_configured():
        raise GoogleOAuthError("Google連携が未設定です。")
    data = {
        "code": code,
        "client_id": client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    return _post_token(data)


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """refresh_token から新しい access_token を取得する。"""
    if not is_configured():
        raise GoogleOAuthError("Google連携が未設定です。")
    if not refresh_token:
        raise GoogleOAuthError("refresh_token がありません。再接続が必要です。")
    data = {
        "refresh_token": refresh_token,
        "client_id": client_id(),
        "client_secret": _client_secret(),
        "grant_type": "refresh_token",
    }
    return _post_token(data)


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """表示用にメールアドレス等を取得する。失敗しても致命的ではない。"""
    try:
        resp = requests.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        logger.warning("Google userinfo の取得に失敗しました", exc_info=True)
    return {}


def revoke(token: str) -> bool:
    """トークンを失効させる。失敗は無視（接続解除はDB側で行う）。"""
    if not token:
        return False
    try:
        resp = requests.post(
            REVOKE_ENDPOINT,
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_HTTP_TIMEOUT,
        )
        return resp.status_code in (200, 400)
    except requests.RequestException:
        logger.warning("Google トークンの失効に失敗しました", exc_info=True)
        return False


def _post_token(data: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = requests.post(TOKEN_ENDPOINT, data=data, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"Googleへの接続に失敗しました: {exc}") from exc
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error_description") or resp.json().get("error") or ""
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise GoogleOAuthError(f"トークン取得に失敗しました: {detail or resp.status_code}")
    return resp.json()
