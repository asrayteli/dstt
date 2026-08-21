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
deployed=$(git rev-parse HEAD)
"$venv/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
"$venv/bin/python" -m compileall -q app scripts
"$venv/bin/python" scripts/predeploy_check.py

pidfile=/home/asray/.local/run/dstt.pid
if [[ ! -s "$pidfile" ]]; then
    echo "Gunicorn PID file is missing; use systemctl restart dstt" >&2
    exit 1
fi
master_pid=$(cat "$pidfile")
old_workers=$(pgrep -P "$master_pid" || true)
kill -HUP "$master_pid"

workers_reloaded=false
for attempt in {1..30}; do
    current_workers=$(pgrep -P "$master_pid" || true)
    old_worker_still_running=false
    for old_worker in $old_workers; do
        if grep -qx "$old_worker" <<<"$current_workers"; then
            old_worker_still_running=true
            break
        fi
    done
    if [[ -n "$current_workers" && "$old_worker_still_running" == false ]] && \
        curl --fail --silent --show-error \
            --resolve dstt.dipalette.com:443:127.0.0.1 \
            https://dstt.dipalette.com/health/ready >/dev/null; then
        workers_reloaded=true
        break
    fi
    sleep 1
done

if [[ "$workers_reloaded" != true ]]; then
    echo "Deployment worker reload/health check failed; previous revision was $previous" >&2
    exit 1
fi

verify_public_asset() {
    local path=$1
    local relative=${path#app/static/}
    local expected encoded actual
    expected=$(sha256sum "$path" | awk '{print $1}')
    encoded=$(
        "$venv/bin/python" -c \
            'import sys; from urllib.parse import quote; print(quote(sys.argv[1]))' \
            "$relative"
    )
    actual=$(
        curl --fail --silent --show-error \
            "https://dstt.dipalette.com/static/${encoded}?v=${expected:0:16}" \
            | sha256sum | awk '{print $1}'
    )
    if [[ "$actual" != "$expected" ]]; then
        echo "Public static asset mismatch: $relative (expected=$expected actual=$actual)" >&2
        return 1
    fi
    printf 'static_asset=ok path=%s hash=%s\n' "$relative" "$expected"
}

declare -A assets_to_verify=(
    [app/static/shiftersync/js/ss_common.js]=1
    [app/static/shiftersync/css/ss_common.css]=1
)
while IFS= read -r -d '' changed_asset; do
    [[ -f "$changed_asset" ]] && assets_to_verify["$changed_asset"]=1
done < <(git diff --name-only -z "$previous" "$deployed" -- app/static)

for asset in "${!assets_to_verify[@]}"; do
    verify_public_asset "$asset"
done

printf 'deployed=%s previous=%s health=ok workers=reloaded static_assets=ok\n' \
    "$deployed" "$previous"
