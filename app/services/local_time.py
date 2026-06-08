"""アプリ共通のローカル時刻ヘルパー。

ToBell の due_at などユーザー入力/カレンダー由来の日時は「設定タイムゾーン
（既定 Asia/Tokyo）のウォールクロックをナイーブ値として保存する」規約になっている
（to_bell_calendar / to_bell_calendar_import 参照）。一方、通知の期日判定などの
「現在時刻」を ``datetime.now()`` で取ると、サーバー（特に UTC のコンテナ）の
タイムゾーンに依存し、保存済み due_at と時差が生じて通知がずれる。

そこで現在時刻もここで一元的に「設定タイムゾーンのナイーブ値」として取得し、
保存値と比較基準を一致させる。
"""
from __future__ import annotations

import os
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo は Py3.9+ 標準
    ZoneInfo = None


def app_timezone() -> str:
    return (os.environ.get("DSTT_TIMEZONE") or "Asia/Tokyo").strip() or "Asia/Tokyo"


def local_now() -> datetime:
    """設定タイムゾーン（既定 Asia/Tokyo）の現在時刻をナイーブ値で返す。

    サーバーの OS タイムゾーンに依存しないため、UTC コンテナ上でも due_at 等の
    ローカル保存値と整合した期日判定ができる。
    """
    if ZoneInfo is None:
        return datetime.now()
    try:
        return datetime.now(ZoneInfo(app_timezone())).replace(tzinfo=None)
    except Exception:
        # 不正な DSTT_TIMEZONE 指定時はサーバーローカルにフォールバック。
        return datetime.now()
