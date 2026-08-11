import multiprocessing
import os

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048


def _worker_count() -> int:
    """ワーカー数を決める。

    従来は `cpu_count() * 2 + 1` 固定だったが、DSTT のワーカーは 1 プロセス
    あたり実測 **約 230MB**（pandas / PyMuPDF などを読み込むため）を使う。
    小型機（例: 4コア/8GB）では 9 ワーカー ≒ 2.1GB を占め、しかも sync
    ワーカーなので 4 コアに対して過剰である。

    そこで「CPU数 + 1」を基本とし、上限を 9 に抑える。目安のメモリ使用量:
      4コア → 5 ワーカー ≒ 1.2GB
      8コア → 9 ワーカー ≒ 2.1GB
    明示指定したい場合は環境変数 DSTT_GUNICORN_WORKERS で上書きできる。
    """
    raw = os.environ.get("DSTT_GUNICORN_WORKERS")
    if raw:
        try:
            explicit = int(raw)
            if explicit >= 1:
                return explicit
        except ValueError:
            pass  # 不正値は既定にフォールバック（起動は止めない）
    return max(2, min(multiprocessing.cpu_count() + 1, 9))


# Worker processes
workers = _worker_count()
worker_class = "sync"
worker_connections = 1000
# Allow long-running downloads. Sync workers stay busy for the whole transfer,
# so this needs to cover a worst-case slow client receiving a multi-GB share.
timeout = 600
keepalive = 2

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 100

# NOTE: worker_tmp_dir はあえて既定（tempfile.gettempdir()）のままにする。
# 以前 "/dev/shm" を指定したが、権限を drop した user から /dev/shm へ
# heartbeat ファイルを書けない/存在しない本番環境では、ワーカーが起動直後に
# クラッシュしてソケットは開くのに応答できず 502 になった。tmpfs 最適化より
# 起動の確実性を優先する。

# Under systemd, stdout/stderr are collected and rotated by journald. Paths can
# still be supplied explicitly for non-systemd deployments.
errorlog = os.environ.get("DSTT_GUNICORN_ERRORLOG", "-")
accesslog = os.environ.get("DSTT_GUNICORN_ACCESSLOG", "-")
capture_output = True
loglevel = "info"

# Process naming
proc_name = 'dstt'

# Server mechanics
daemon = False
pidfile = '/var/run/dstt.pid'
user = 'asray'
group = 'asray'
tmp_upload_dir = None

# SSL (if needed later)
# keyfile = "/path/to/keyfile"
# certfile = "/path/to/certfile"
