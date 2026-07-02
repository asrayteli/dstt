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

# Worker heartbeat files on tmpfs to avoid disk I/O stalls blocking the
# arbiter's liveness checks (a common cause of spurious worker timeouts).
worker_tmp_dir = "/dev/shm"

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
