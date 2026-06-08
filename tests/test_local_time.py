"""設定タイムゾーン基準の現在時刻ヘルパー local_now() の検証。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.local_time import app_timezone, local_now


def test_default_timezone_is_tokyo(monkeypatch):
    monkeypatch.delenv("DSTT_TIMEZONE", raising=False)
    assert app_timezone() == "Asia/Tokyo"


def test_local_now_is_jst_regardless_of_server_tz(monkeypatch):
    # 既定(Asia/Tokyo)では UTC より約9時間進んでいる。サーバーのOSタイムゾーンに
    # 依存せず、設定TZで決まることを確認する。
    monkeypatch.delenv("DSTT_TIMEZONE", raising=False)
    diff = local_now() - datetime.utcnow()
    hours = diff.total_seconds() / 3600
    assert 8.5 < hours < 9.5


def test_local_now_follows_dstt_timezone(monkeypatch):
    monkeypatch.setenv("DSTT_TIMEZONE", "UTC")
    diff = local_now() - datetime.utcnow()
    assert abs(diff.total_seconds()) < 60


def test_invalid_timezone_falls_back(monkeypatch):
    monkeypatch.setenv("DSTT_TIMEZONE", "Not/AZone")
    # 例外を投げずローカル時刻にフォールバックする。
    assert isinstance(local_now(), datetime)
