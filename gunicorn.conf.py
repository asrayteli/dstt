import multiprocessing

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 100

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
