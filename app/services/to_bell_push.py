from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta

from app.services.local_time import local_now
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import ToBellPushDelivery, ToBellPushSubscription, ToBellTask, db
from app.services.to_bell_service import has_explicit_notification_time, task_notification_targets

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover
    BackgroundScheduler = None

try:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover
    Vapid = None
    WebPushException = Exception
    b64urlencode = None
    serialization = None
    webpush = None


logger = logging.getLogger(__name__)
_scheduler = None
_scheduler_lock = threading.Lock()

try:
    import fcntl  # Linux/Unix のみ。プロセス間ロックに使用。
except Exception:  # pragma: no cover - Windows 等
    fcntl = None


def _run_singleton(app, lock_filename: str, job) -> None:
    """gunicorn の複数ワーカーで同一ジョブが多重実行されないよう、OS の
    ファイルロック（flock）で「各実行を1プロセスだけ」に限定して job を呼ぶ。

    - ロックを取得できたプロセスのみが job を実行し、他プロセスは即スキップする。
    - ロックは実行中のみ保持し終了時に解放するため、ロック保持プロセスが
      再起動（gunicorn の max_requests 等）で消えても、次回tickで別プロセスが
      取得でき、単一障害点にならない。
    - fcntl が無い環境（Windows 等）ではロックなしで実行（従来動作）。
    """
    if fcntl is None:
        job()
        return
    lock_path = os.path.join(app.instance_path, lock_filename)
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        handle = open(lock_path, "w")
    except OSError:
        # ロックファイルを用意できない場合はロックなしで実行する。
        job()
        return
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # 他プロセスが実行中 → このtickはスキップ
        job()
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:
            pass
        handle.close()


class ToBellPushUnavailable(RuntimeError):
    pass


def vapid_public_key() -> str:
    vapid = _load_or_create_vapid()
    if serialization is None or b64urlencode is None:
        raise ToBellPushUnavailable("Web Push ライブラリが利用できません。")
    public_bytes = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(public_bytes)


def save_subscription(user_id: str, payload: dict[str, Any], *, user_agent: str = "") -> ToBellPushSubscription:
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("購読情報が不正です。")
    row = ToBellPushSubscription.query.filter_by(endpoint=endpoint).first()
    if row is None:
        row = ToBellPushSubscription(endpoint=endpoint)
        db.session.add(row)
    row.user_id = user_id
    row.p256dh = p256dh
    row.auth = auth
    row.user_agent = user_agent[:500]
    row.device_label = _device_label(user_agent)
    row.is_active = True
    db.session.commit()
    return row


def unsubscribe(user_id: str, endpoint: str) -> bool:
    row = ToBellPushSubscription.query.filter_by(endpoint=endpoint, user_id=user_id).first()
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def list_subscriptions(user_id: str) -> list[dict[str, Any]]:
    rows = ToBellPushSubscription.query.filter_by(user_id=user_id).order_by(
        ToBellPushSubscription.is_active.desc(),
        ToBellPushSubscription.updated_at.desc(),
    ).all()
    return [_subscription_payload(row) for row in rows]


def update_subscription_label(user_id: str, subscription_id: int, label: str) -> dict[str, Any]:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        raise ValueError("通知先が見つかりません。")
    row.device_label = str(label or "").strip()[:120] or _device_label(row.user_agent)
    db.session.commit()
    return _subscription_payload(row)


def deactivate_subscription(user_id: str, subscription_id: int) -> bool:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        return False
    row.is_active = False
    db.session.commit()
    return True


def delete_subscription(user_id: str, subscription_id: int) -> bool:
    row = ToBellPushSubscription.query.filter_by(id=subscription_id, user_id=user_id).first()
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def send_test_push(user_id: str) -> dict[str, int]:
    return send_push_to_user(
        user_id,
        title="ToBell テスト通知 from DSTT",
        body="この端末のプッシュ通知は有効です。",
        url="/tools/to_bell",
    )


def send_due_task_pushes(*, now: datetime | None = None) -> dict[str, int]:
    # due_at は利用者が入力したローカル時刻のナイーブな値として保存されるため、
    # 比較も設定タイムゾーン基準の現在時刻を使う（サーバーTZ依存で通知がずれる不具合を防ぐ）。
    now = now or local_now()
    cutoff = now - timedelta(days=60)
    tasks = ToBellTask.query.filter(
        ToBellTask.status.notin_(["done", "archived"]),
        ToBellTask.due_at.isnot(None),
        ToBellTask.due_at <= now,
        ToBellTask.due_at >= cutoff,
    ).all()
    sent = 0
    skipped = 0
    failed = 0
    for task in tasks:
        if not has_explicit_notification_time(task):
            skipped += 1
            continue
        due_key = task.due_at.isoformat(timespec="minutes") if task.due_at else ""
        for user_id in task_notification_targets(task):
            delivery = ToBellPushDelivery.query.filter_by(
                task_id=task.id,
                user_id=user_id,
                due_at_key=due_key,
            ).first()
            if delivery is not None and delivery.status != "failed":
                skipped += 1
                continue
            # 先にレコードを確保してから送信する。gunicorn の複数ワーカーが
            # 同時に実行した場合、最初に INSERT/flush に成功したワーカーだけが
            # 送信を行い、他ワーカーは IntegrityError でスキップする。
            if delivery is None:
                delivery = ToBellPushDelivery(
                    task_id=task.id,
                    user_id=user_id,
                    due_at_key=due_key,
                    status="pending",
                )
                db.session.add(delivery)
                try:
                    db.session.flush()
                except IntegrityError:
                    db.session.rollback()
                    skipped += 1
                    continue
            result = send_push_to_user(
                user_id,
                title=f"ToBell {task.title} from DSTT",
                body=task.description or "",
                url=f"/tools/to_bell?task={task.id}",
            )
            sent += result["sent"]
            failed += result["failed"]
            if result["sent"]:
                delivery.status = "sent"
            elif result["failed"]:
                delivery.status = "failed"
            else:
                delivery.status = "skipped"
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
    return {"sent": sent, "skipped": skipped, "failed": failed}


# push サービスが「この購読はもう成功しない」と示すサイン。
# 404/410 : 購読そのものが失効している（購読解除・ブラウザ再インストール等）
# 403     : VAPID 署名がこの購読を認可しない。サーバ側の鍵を入れ替えない限り
#           永久に失敗するため、リトライしても無駄（毎分の再送で時間を浪費する）
_PUSH_GONE_STATUSES = {404, 410}
_PUSH_VAPID_STATUSES = {403}
# 400 は原因が幅広い（こちらのペイロード不備など一時的なものも含む）ため、
# VAPID 鍵の不一致だと明示されている場合だけ恒久失敗とみなす。
_PUSH_VAPID_HINTS = (
    "vapidpkhashmismatch",
    "vapid credentials in the authorization header do not correspond",
)


def _is_permanent_push_failure(exc, status_code) -> bool:
    """この購読を無効化すべき（リトライしても成功しない）失敗かを判定する。

    一時障害（5xx やネットワーク断）や原因不明の 400 で購読を消すと、
    利用者は理由が分からないまま通知が止まり、再登録するまで復旧しない。
    そのため「恒久的だと確信できる場合」だけ True を返す。

    既知のトレードオフ: サーバ側の VAPID 鍵ファイル（instance/ 配下）を
    失って再生成した場合、既存の購読は一斉に 403/400 になり、まとめて
    is_active=False になる。その状態では実際どの購読にも配信できないため
    判定としては正しいが、鍵をバックアップから戻した場合は無効化された
    購読も戻す必要がある:
        UPDATE to_bell_push_subscriptions SET is_active = 1;
    無効化は削除ではなくフラグなので、この 1 文で復旧できる。
    """
    if status_code in _PUSH_GONE_STATUSES:
        return True
    if status_code in _PUSH_VAPID_STATUSES:
        return True
    if status_code == 400:
        detail = " ".join(filter(None, (
            str(exc or ""),
            str(getattr(getattr(exc, "response", None), "text", "") or ""),
        ))).lower()
        return any(hint in detail for hint in _PUSH_VAPID_HINTS)
    return False


def send_push_to_user(user_id: str, *, title: str, body: str, url: str) -> dict[str, int]:
    if webpush is None:
        raise ToBellPushUnavailable("pywebpush がインストールされていません。")
    private_key_path = _vapid_private_key_path()
    if not private_key_path.exists():
        _load_or_create_vapid()
    subscriptions = ToBellPushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    sent = 0
    failed = 0
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
            "icon": "/static/img/android-chrome-192x192.png",
            "badge": "/static/img/apple-touch-icon.png",
        },
        ensure_ascii=False,
    )
    subject = _vapid_subject()
    for subscription in subscriptions:
        # pywebpush は渡した claims を破壊的に書き換える（配信先の origin を aud、
        # 期限を exp として追記する）。dict を使い回すと 2 件目以降の aud が
        # 1 件目の配信先のままになり、別の push サービス（例: iPhone は
        # web.push.apple.com、PC は fcm.googleapis.com）で署名が一致せず失敗する。
        # そのため購読ごとに新しい claims を渡す。
        claims = {"sub": subject}
        try:
            webpush(
                subscription_info=subscription.to_subscription_info(),
                data=payload,
                vapid_private_key=str(private_key_path),
                vapid_claims=claims,
                ttl=3600,
                timeout=8,
            )
            sent += 1
        except WebPushException as exc:
            failed += 1
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if _is_permanent_push_failure(exc, status_code):
                # commit で属性が expire されるため、ログ用の値は先に控える
                endpoint = (subscription.endpoint or "")[:80]
                subscription.is_active = False
                db.session.commit()
                logger.info(
                    "To Bell push: 恒久的な失敗のため購読を無効化しました "
                    "(user=%s, status=%s, endpoint=%s)",
                    user_id, status_code, endpoint,
                )
            else:
                logger.warning("To Bell push failed for %s: %s", user_id, exc)
        except Exception as exc:  # noqa: BLE001 - 1件の配信失敗で全体を止めない
            failed += 1
            logger.warning("To Bell push error for %s: %s", user_id, exc)
    return {"sent": sent, "failed": failed}


def init_to_bell_push_scheduler(app) -> None:
    if app.config.get("TESTING"):
        return
    if not _config_bool(app, "TO_BELL_PUSH_SCHEDULER_ENABLED", "DSTT_TO_BELL_PUSH_SCHEDULER", True):
        return
    if BackgroundScheduler is None:
        logger.warning("APScheduler is not installed; To Bell push scheduler is disabled")
        return

    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            func=lambda: _run_due_push_job(app),
            trigger="interval",
            minutes=1,
            id="to_bell_due_pushes",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        # Google Calendar → ToBell の取り込み。15分間隔で1度だけ起床し、
        # 各ユーザーの選択間隔（手動/15分〜1日）に達した人だけを差分取得で処理する。
        _scheduler.add_job(
            func=lambda: _run_gcal_import_job(app),
            trigger="interval",
            minutes=15,
            id="to_bell_gcal_import",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        # 健診PLUSのリマインド同期。対象者数に比例して重い（800名で初回13秒）ため、
        # リクエスト経路（before_request）ではなくここで回す。30分ごとに起床し、
        # その日まだ実行していなければ1回だけ実行する。
        _scheduler.add_job(
            func=lambda: _run_health_check_sweep_job(app),
            trigger="interval",
            minutes=30,
            id="health_check_daily_sweep",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        _scheduler.start()


def _run_health_check_sweep_job(app) -> None:
    """健診PLUSのリマインドを1日1回だけ同期する。

    実行済みかどうかは instance 配下のマーカーファイルの日付で判定する
    （gunicorn の複数ワーカー対策は _run_singleton の flock と併用）。
    """
    def _job():
        marker = os.path.join(app.instance_path, "health_check_sweep_last_run")
        # アプリの運用タイムゾーンで日付を判定する。OSがUTCでも日本時間の
        # 日付境界で日次実行されるようにする。
        today = local_now().date().isoformat()
        try:
            with open(marker, "r", encoding="utf-8") as f:
                if f.read().strip() == today:
                    return
        except OSError:
            pass
        with app.app_context():
            try:
                from app.services.to_bell_hooks import sweep_health_check_reminders
                result = sweep_health_check_reminders()
                logger.info("health_check daily sweep: %s", result)
                if result.get("failed", 0):
                    # 失敗を実行済み扱いにせず、次の30分tickで再試行する。
                    logger.warning("health_check daily sweep will retry: %s", result)
                    return
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("health_check daily sweep failed: %s", exc)
                return
        try:
            os.makedirs(app.instance_path, exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                f.write(today)
        except OSError:
            logger.warning("health_check sweep marker を書けませんでした: %s", marker)

    _run_singleton(app, "health_check_daily_sweep.lock", _job)


def _run_due_push_job(app) -> None:
    def _job():
        with app.app_context():
            try:
                send_due_task_pushes()
            except Exception as exc:
                db.session.rollback()
                logger.warning("To Bell push scheduler failed: %s", exc)
    _run_singleton(app, "to_bell_due_pushes.lock", _job)


def _run_gcal_import_job(app) -> None:
    def _job():
        with app.app_context():
            try:
                from app.services.to_bell_calendar_import import run_due_imports

                run_due_imports()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                logger.warning("To Bell calendar import scheduler failed: %s", exc)
    _run_singleton(app, "to_bell_gcal_import.lock", _job)


def _load_or_create_vapid():
    if Vapid is None:
        raise ToBellPushUnavailable("Web Push ライブラリが利用できません。")
    private_path = _vapid_private_key_path()
    public_path = _vapid_public_key_path()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    vapid = Vapid()
    if private_path.exists():
        return vapid.from_pem(private_path.read_bytes())
    vapid.generate_keys()
    private_path.write_bytes(vapid.private_pem())
    public_path.write_bytes(vapid.public_pem())
    return vapid


def _vapid_private_key_path() -> Path:
    configured = current_app.config.get("TO_BELL_VAPID_PRIVATE_KEY_PATH") or os.environ.get("DSTT_TO_BELL_VAPID_PRIVATE_KEY_PATH")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "to_bell_vapid_private.pem"


def _vapid_public_key_path() -> Path:
    configured = current_app.config.get("TO_BELL_VAPID_PUBLIC_KEY_PATH") or os.environ.get("DSTT_TO_BELL_VAPID_PUBLIC_KEY_PATH")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "to_bell_vapid_public.pem"


def _vapid_subject() -> str:
    return (
        current_app.config.get("TO_BELL_VAPID_SUBJECT")
        or os.environ.get("DSTT_TO_BELL_VAPID_SUBJECT")
        or "mailto:admin@example.com"
    )


def _device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "windows" in ua:
        return "Windows"
    return "Web Push"


def _subscription_payload(row: ToBellPushSubscription) -> dict[str, Any]:
    endpoint = row.endpoint or ""
    return {
        "id": row.id,
        "device_label": row.device_label or _device_label(row.user_agent),
        "user_agent": row.user_agent or "",
        "endpoint_tail": endpoint[-18:] if endpoint else "",
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _config_bool(app, key: str, env_key: str, default: bool = True) -> bool:
    value = app.config.get(key)
    if value is None:
        value = os.environ.get(env_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
