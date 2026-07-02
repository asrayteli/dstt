import multiprocessing

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
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

# Log files
errorlog = "/var/log/dstt/error.log"
accesslog = "/var/log/dstt/access.log"
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
