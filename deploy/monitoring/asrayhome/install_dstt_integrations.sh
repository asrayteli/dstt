#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo." >&2
    exit 1
fi

target_user=${DSTT_MONITOR_USER:-${SUDO_USER:-asray}}
target_home=$(getent passwd "$target_user" | cut -d: -f6)
target_uid=$(id -u "$target_user")
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$source_dir/../../.." && pwd)
push_source=/var/lib/uptime-kuma/dstt-monitor-push.env

if [[ -z "$target_home" || ! -d "$target_home" ]]; then
    echo "Unable to resolve the home directory for $target_user" >&2
    exit 1
fi
if [[ ! -s "$push_source" ]]; then
    echo "Provision Uptime Kuma monitors before installing integrations." >&2
    exit 1
fi

install -d -o "$target_user" -g "$target_user" -m 0700 \
    "$target_home/.config/dstt-monitoring" \
    "$target_home/.config/systemd/user" \
    "$target_home/.local/lib/dstt"
install -o "$target_user" -g "$target_user" -m 0600 \
    "$push_source" "$target_home/.config/dstt-monitoring/push.env"
install -o "$target_user" -g "$target_user" -m 0755 \
    "$source_dir/dstt_internal_probe.py" "$target_home/.local/lib/dstt/dstt_internal_probe.py"
install -o "$target_user" -g "$target_user" -m 0755 \
    "$repository_root/scripts/sync_dstt_backup.sh" "$target_home/.local/lib/dstt/sync_dstt_backup.sh"
install -o "$target_user" -g "$target_user" -m 0644 \
    "$source_dir/dstt-internal-health.service" \
    "$source_dir/dstt-internal-health.timer" \
    "$repository_root/deploy/systemd-user/dstt-backup-offsite.service" \
    "$repository_root/deploy/systemd-user/dstt-backup-offsite.timer" \
    "$target_home/.config/systemd/user/"

runtime_dir=/run/user/$target_uid
if [[ ! -d "$runtime_dir" ]]; then
    loginctl enable-linger "$target_user"
fi

sudo -u "$target_user" XDG_RUNTIME_DIR="$runtime_dir" systemctl --user daemon-reload
sudo -u "$target_user" XDG_RUNTIME_DIR="$runtime_dir" systemctl --user enable --now \
    dstt-internal-health.timer dstt-backup-offsite.timer
sudo -u "$target_user" XDG_RUNTIME_DIR="$runtime_dir" systemctl --user start dstt-internal-health.service

echo "DSTT internal and off-site-backup monitoring integrations are active."
