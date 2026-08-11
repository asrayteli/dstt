#!/usr/bin/env bash
set -euo pipefail

remote_host=${DSTT_BACKUP_REMOTE_HOST:-home-server}
remote_root=${DSTT_BACKUP_REMOTE_ROOT:-/home/asray/backups/dstt}
destination=${DSTT_OFFSITE_BACKUP_DIR:-/home/asray/backups/dstt/dipalette}
verifier=${DSTT_BACKUP_VERIFIER:-/home/asray/.local/lib/dstt/backup_dstt.py}
keep=${DSTT_OFFSITE_BACKUP_KEEP:-30}
push_url=${DSTT_BACKUP_PUSH_URL:-}

notify_push() {
    local status=$1
    local message=$2
    if [[ -z "$push_url" ]]; then
        return 0
    fi
    curl --fail --silent --show-error --max-time 15 --get \
        --data-urlencode "status=$status" \
        --data-urlencode "msg=$message" \
        --data-urlencode "ping=0" \
        "$push_url" >/dev/null
}

case "$keep" in
    ''|*[!0-9]*) echo "DSTT_OFFSITE_BACKUP_KEEP must be a positive integer" >&2; exit 2 ;;
esac
if (( keep < 1 )); then
    echo "DSTT_OFFSITE_BACKUP_KEEP must be at least 1" >&2
    exit 2
fi

umask 077
mkdir -p "$destination"
latest=$(ssh -o BatchMode=yes "$remote_host" "cat '$remote_root/LATEST'")
if [[ ! "$latest" =~ ^snapshot-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{8})?$ ]]; then
    echo "Remote returned an unsafe snapshot name" >&2
    exit 1
fi

final="$destination/$latest"
if [[ -d "$final" ]]; then
    python3 "$verifier" --verify-only "$final"
    notify_push up "verified existing off-site backup $latest"
    exit 0
fi

staging=$(mktemp -d "$destination/.partial-$latest.XXXXXX")
cleanup() {
    local status=$?
    if [[ -n "${staging:-}" && -d "$staging" && "$staging" == "$destination"/.partial-* ]]; then
        rm -rf -- "$staging"
    fi
    if (( status != 0 )); then
        notify_push down "off-site backup synchronization failed (exit $status)" || true
    fi
    return "$status"
}
trap cleanup EXIT

previous=$(find "$destination" -mindepth 1 -maxdepth 1 -type d -name 'snapshot-*' -print | sort | tail -n 1)
rsync_options=(-a --protect-args)
if [[ -n "$previous" ]]; then
    rsync_options+=(--link-dest="$previous")
fi
rsync "${rsync_options[@]}" "$remote_host:$remote_root/$latest/" "$staging/"
python3 "$verifier" --verify-only "$staging"
mv -- "$staging" "$final"
staging=''

mapfile -t snapshots < <(
    find "$destination" -mindepth 1 -maxdepth 1 -type d -name 'snapshot-*' -print | sort -r
)
for old in "${snapshots[@]:$keep}"; do
    if [[ "$(dirname -- "$old")" != "$destination" || ! "$(basename -- "$old")" =~ ^snapshot- ]]; then
        echo "Refusing to prune unexpected path: $old" >&2
        exit 1
    fi
    rm -rf -- "$old"
done

python3 "$verifier" --verify-only "$final"
notify_push up "copied and verified off-site backup $latest"
