"""gunicorn.conf.py のワーカー数決定ロジックのテスト。

1ワーカーあたり実測約230MB使うため、小型機でワーカーが増えすぎないこと、
および環境変数で明示的に上書きできることを担保する。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_conf(monkeypatch, *, cpu, env=None):
    import multiprocessing
    monkeypatch.setattr(multiprocessing, "cpu_count", lambda: cpu)
    for key in ("DSTT_GUNICORN_WORKERS",):
        monkeypatch.delenv(key, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("dstt_gunicorn_conf", ROOT / "gunicorn.conf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("cpu,expected", [
    (1, 2),    # 最低2は確保する
    (2, 3),
    (4, 5),    # N150 等の小型機: 5ワーカー ≒ 1.2GB
    (8, 9),
    (12, 9),   # 上限9で頭打ち（従来は25になり ≒5.8GB を要求していた）
    (32, 9),
])
def test_worker_count_scales_and_is_capped(monkeypatch, cpu, expected):
    mod = _load_conf(monkeypatch, cpu=cpu)
    assert mod.workers == expected


def test_env_var_overrides_worker_count(monkeypatch):
    mod = _load_conf(monkeypatch, cpu=4, env={"DSTT_GUNICORN_WORKERS": "3"})
    assert mod.workers == 3


@pytest.mark.parametrize("bad", ["0", "-1", "abc", ""])
def test_invalid_env_var_falls_back_to_default(monkeypatch, bad):
    """不正な指定で起動を止めない（既定値にフォールバックする）。"""
    mod = _load_conf(monkeypatch, cpu=4, env={"DSTT_GUNICORN_WORKERS": bad})
    assert mod.workers == 5


def test_other_settings_are_unchanged(monkeypatch):
    """ワーカー数以外の設定を壊していないこと。"""
    mod = _load_conf(monkeypatch, cpu=4)
    assert mod.worker_class == "sync"
    assert mod.timeout == 600
    assert mod.bind == "127.0.0.1:5000"
    assert mod.max_requests == 1000
    assert mod.proc_name == "dstt"
    assert mod.errorlog == "-"
    assert mod.accesslog == "-"
    assert mod.capture_output is True
