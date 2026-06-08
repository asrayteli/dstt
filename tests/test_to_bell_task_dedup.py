"""ToBell 連携タスクの一意制約とデデュープ移行を検証する。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    from app import create_app
    from app.models import db

    db_path = tmp_path / "to_bell_dedup.db"
    monkeypatch.chdir(tmp_path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


def _task(**kw):
    from app.models import ToBellTask

    defaults = dict(title="t", created_by="alice", status="todo", priority="normal")
    defaults.update(kw)
    return ToBellTask(**defaults)


def test_unique_index_blocks_duplicate_source_tasks(app_ctx):
    from app.models import db

    with app_ctx.app_context():
        db.session.add(_task(source_tool="google", source_ref_type="calendar_event", source_ref_id="alice:e1"))
        db.session.commit()
        # 同一参照の2件目は一意制約で拒否される。
        db.session.add(_task(source_tool="google", source_ref_type="calendar_event", source_ref_id="alice:e1"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_savepoint_recovery_on_concurrent_duplicate(app_ctx):
    """to_bell_hooks の二重生成回復パスの中核（add+flush を begin_nested 内で行い、
    IntegrityError 時に savepoint を巻き戻して既存を採用する）を検証する。

    add を begin_nested の外に出すと巻き戻し後にセッションが復旧できず
    PendingRollbackError になる回帰を防ぐ。
    """
    from sqlalchemy.exc import IntegrityError as IE

    from app.models import db, ToBellTask

    with app_ctx.app_context():
        src = dict(source_tool="hk", source_ref_type="reminder", source_ref_id="rec:1")
        db.session.add(_task(title="A", **src))
        db.session.commit()

        # 別プロセスが先に同一参照タスクを作った状況を再現（事前チェックをすり抜けた想定）。
        task = _task(title="B", **src)
        try:
            with db.session.begin_nested():
                db.session.add(task)
                db.session.flush()
            recovered = None
        except IE:
            recovered = ToBellTask.query.filter_by(**src).first()

        # 既存タスクを採用でき、セッションは健全（commit 可能）、重複は増えない。
        assert recovered is not None and recovered.title == "A"
        db.session.commit()
        assert ToBellTask.query.filter_by(**src).count() == 1


def test_create_task_for_is_idempotent(app_ctx):
    """_create_task_for を同一参照で2回呼んでも1件のみ（事前チェック経路）。"""
    from app.models import db, ToBellTask, ToBellNotification
    from app.services.to_bell_hooks import _create_task_for

    with app_ctx.app_context():
        kw = dict(target_user="alice", title="健診リマインド", source_tool="health_check",
                  source_ref_type="reminder", source_ref_id="rec:9")
        first = _create_task_for(**kw)
        db.session.commit()
        second = _create_task_for(**kw)
        db.session.commit()
        assert first.id == second.id
        assert ToBellTask.query.filter_by(source_tool="health_check", source_ref_id="rec:9").count() == 1
        # 通知も二重に作られない。
        assert ToBellNotification.query.filter_by(task_id=first.id).count() == 1


def test_manual_tasks_with_null_source_are_unaffected(app_ctx):
    from app.models import db, ToBellTask

    with app_ctx.app_context():
        # source_ref_id が NULL の手動タスクは何件でも作れる（NULL は distinct）。
        for _ in range(3):
            db.session.add(_task())
        db.session.commit()
        assert ToBellTask.query.count() == 3


def test_dedupe_leaves_rows_with_null_components_untouched(app_ctx):
    """source_tool/source_ref_type が NULL の行は一意制約上は別物扱い(NULLは distinct)
    なので、統合対象から除外され削除されないことを確認する（データ損失防止）。"""
    from app import _dedupe_to_bell_task_sources
    from app.models import db, ToBellTask

    with app_ctx.app_context():
        db.session.execute(text("DROP INDEX IF EXISTS uq_to_bell_task_source"))
        db.session.commit()

        # source_tool が NULL の同一見え行を2件（一意制約では衝突しない組み合わせ）。
        db.session.add(_task(title="n1", source_tool=None, source_ref_type="t", source_ref_id="x"))
        db.session.add(_task(title="n2", source_tool=None, source_ref_type="t", source_ref_id="x"))
        db.session.commit()

        removed = _dedupe_to_bell_task_sources()
        assert removed == 0
        assert ToBellTask.query.filter_by(source_ref_id="x").count() == 2


def test_dedupe_merges_duplicates_and_repoints_children(app_ctx):
    from app import _dedupe_to_bell_task_sources
    from app.models import (
        db, ToBellTask, ToBellSubtask, ToBellComment, ToBellNotification,
        ToBellAttachment, ToBellPushDelivery, ToBellTag, ToBellTaskTag,
    )

    with app_ctx.app_context():
        # 一意インデックスを一旦外して重複を仕込む（既存DBの汚れを再現）。
        db.session.execute(text("DROP INDEX IF EXISTS uq_to_bell_task_source"))
        db.session.commit()

        src = dict(source_tool="cloudshift", source_ref_type="substitute_update", source_ref_id="alice:7:20260601")
        keep = _task(title="keep", **src)
        dup1 = _task(title="dup1", **src)
        dup2 = _task(title="dup2", **src)
        db.session.add_all([keep, dup1, dup2])
        db.session.flush()
        keep_id = keep.id
        dup_ids = [dup1.id, dup2.id]

        tag = ToBellTag(name="重要", created_by="alice")
        db.session.add(tag)
        db.session.flush()

        # 各種子レコードを重複側にぶら下げる（衝突ケースも含む）。
        db.session.add(ToBellSubtask(task_id=dup1.id, title="s1"))
        db.session.add(ToBellComment(task_id=dup1.id, body="c1", created_by="alice"))
        db.session.add(ToBellNotification(task_id=dup2.id, user_id="alice", title="n1"))
        db.session.add(ToBellAttachment(task_id=dup1.id, file_name="f", stored_path="/p", uploaded_by="alice"))
        # プッシュ配信: keep と dup1 で同一キー → 衝突するので dup 側は破棄される。
        db.session.add(ToBellPushDelivery(task_id=keep_id, user_id="alice", due_at_key="k1"))
        db.session.add(ToBellPushDelivery(task_id=dup1.id, user_id="alice", due_at_key="k1"))
        db.session.add(ToBellPushDelivery(task_id=dup2.id, user_id="alice", due_at_key="k2"))
        # タグ: keep と dup1 が同じタグ → 衝突するので dup 側は破棄される。
        db.session.add(ToBellTaskTag(task_id=keep_id, tag_id=tag.id))
        db.session.add(ToBellTaskTag(task_id=dup1.id, tag_id=tag.id))
        db.session.commit()

        removed = _dedupe_to_bell_task_sources()
        assert removed == 2

        # 重複タスクは消え、keep のみが残る。
        remaining = ToBellTask.query.filter_by(**src).all()
        assert [t.id for t in remaining] == [keep_id]

        # 単純な子は keep へ付け替えられる。
        assert ToBellSubtask.query.filter_by(task_id=keep_id).count() == 1
        assert ToBellComment.query.filter_by(task_id=keep_id).count() == 1
        assert ToBellNotification.query.filter_by(task_id=keep_id).count() == 1
        assert ToBellAttachment.query.filter_by(task_id=keep_id).count() == 1

        # プッシュ配信: k1(衝突)は1件のまま, k2 が移送され合計2件。
        deliveries = ToBellPushDelivery.query.filter_by(task_id=keep_id).all()
        assert sorted(d.due_at_key for d in deliveries) == ["k1", "k2"]

        # タグ: 衝突のため1件のまま。
        assert ToBellTaskTag.query.filter_by(task_id=keep_id).count() == 1

        # 子レコードが旧重複IDに残っていない。
        for child in (ToBellSubtask, ToBellComment, ToBellNotification, ToBellAttachment, ToBellPushDelivery, ToBellTaskTag):
            assert child.query.filter(child.task_id.in_(dup_ids)).count() == 0

        # デデュープ後に一意インデックスを張り直せる。
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_to_bell_task_source "
            "ON to_bell_tasks (source_tool, source_ref_type, source_ref_id)"
        ))
        db.session.commit()
