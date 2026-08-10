"""ユーザー認証用パスワードの最低基準。"""

from __future__ import annotations


MIN_PASSWORD_LENGTH = 4


def password_policy_error(password: str | None, *, username: str = "") -> str | None:
    """4文字未満なら不適合理由を返し、それ以外は ``None`` を返す。

    パスワード教育は会社の運用で行う方針のため、文字種、最大長、ユーザーIDの
    含有、既知パスワードについてソフトウェア側では制限しない。
    ``username`` は既存呼び出しとのAPI互換性のため受け取る。
    """
    del username
    value = password or ""
    if len(value) < MIN_PASSWORD_LENGTH:
        return f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください"
    return None
