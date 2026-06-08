"""ToBell スケジューラの多重起動防止（プロセス間ファイルロック）を検証する。"""

import os
import sys

import pytest

import app.services.to_bell_push as push


@pytest.fixture()
def fake_app(tmp_path):
    class _App:
        instance_path = str(tmp_path)
    return _App()


@pytest.mark.skipif(push.fcntl is None, reason="fcntl 非対応環境")
def test_run_singleton_runs_job_when_lock_free(fake_app):
    calls = []
    push._run_singleton(fake_app, "job.lock", lambda: calls.append(1))
    assert calls == [1]


@pytest.mark.skipif(push.fcntl is None, reason="fcntl 非対応環境")
def test_run_singleton_skips_when_lock_held_by_another(fake_app):
    import fcntl

    lock_path = os.path.join(fake_app.instance_path, "job.lock")
    # 別プロセス相当として、別の open file description で排他ロックを保持する。
    held = open(lock_path, "w")
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        calls = []
        push._run_singleton(fake_app, "job.lock", lambda: calls.append(1))
        # ロックを取得できないため job は実行されない（スキップ）。
        assert calls == []
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()

    # ロック解放後は再度実行できる。
    calls = []
    push._run_singleton(fake_app, "job.lock", lambda: calls.append(1))
    assert calls == [1]


@pytest.mark.skipif(push.fcntl is None, reason="fcntl 非対応環境")
def test_run_singleton_releases_lock_after_job(fake_app):
    push._run_singleton(fake_app, "job.lock", lambda: None)
    # 実行後にロックが解放され、続けて取得できること。
    push._run_singleton(fake_app, "job.lock", lambda: None)


def test_run_singleton_without_fcntl_still_runs(fake_app, monkeypatch):
    # fcntl が無い環境ではロックなしで必ず実行される（従来動作の維持）。
    monkeypatch.setattr(push, "fcntl", None)
    calls = []
    push._run_singleton(fake_app, "job.lock", lambda: calls.append(1))
    assert calls == [1]
