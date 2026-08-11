#!/usr/bin/env bash
set -euo pipefail

project_root=${DSTT_PROJECT_ROOT:-/home/asray/tools/dstt}
venv=${DSTT_VENV:-/home/asray/tools/dstt-ubu}
backup_script=${DSTT_BACKUP_SCRIPT:-/home/asray/.local/lib/dstt/backup_dstt.py}
data_root=${DSTT_DATA_DIR:-/home/asray/.local/share/dstt}
backup_root=${DSTT_BACKUP_DIR:-/home/asray/backups/dstt}
target=${1:-origin/main}

cd "$project_root"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Refusing to deploy over tracked local changes" >&2
    exit 1
fi

previous=$(git rev-parse HEAD)
python3 "$backup_script" \
    --project-root "$project_root" \
    --data-root "$data_root" \
    --database "$data_root/instance/users.db" \
    --destination "$backup_root" \
    --keep 7

git fetch origin
git merge --ff-only "$target"
"$venv/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
"$venv/bin/python" -m compileall -q app scripts
"$venv/bin/python" -m pytest -q \
    tests/test_health_endpoints.py \
    tests/test_postgres_readiness.py \
    tests/test_runtime_paths.py \
    tests/test_static_data_protection.py

pidfile=/home/asray/.local/run/dstt.pid
if [[ ! -s "$pidfile" ]]; then
    echo "Gunicorn PID file is missing; use systemctl restart dstt" >&2
    exit 1
fi
kill -HUP "$(cat "$pidfile")"

for attempt in {1..30}; do
    if curl --fail --silent --show-error \
        --resolve dstt.dipalette.com:443:127.0.0.1 \
        https://dstt.dipalette.com/health/ready >/dev/null; then
        printf 'deployed=%s previous=%s health=ok\n' "$(git rev-parse HEAD)" "$previous"
        exit 0
    fi
    sleep 1
done

echo "Deployment health check failed; previous revision was $previous" >&2
exit 1
