#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo." >&2
    exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_root=${UPTIME_KUMA_INSTALL_ROOT:-/opt/dstt-monitoring}
data_root=${UPTIME_KUMA_DATA_ROOT:-/var/lib/uptime-kuma}
backup_root=${UPTIME_KUMA_BACKUP_ROOT:-/var/backups/uptime-kuma}
library_root=/usr/local/lib/dstt-monitoring
stamp=$(date +%Y%m%d-%H%M%S)
change_backup="/root/dstt-monitoring-install-${stamp}"

for command in docker tailscale python3 curl; do
    command -v "${command}" >/dev/null || {
        echo "Required command not found: ${command}" >&2
        exit 1
    }
done
docker compose version >/dev/null

install -d -m 0700 "${change_backup}"
if [[ -d ${install_root} ]]; then
    cp -a "${install_root}" "${change_backup}/install-root"
fi
tailscale serve get-config --all > "${change_backup}/tailscale-serve.hujson"

install -d -o root -g root -m 0755 "${install_root}" "${library_root}"
install -d -o root -g root -m 0700 "${data_root}" "${backup_root}"
install -o root -g root -m 0644 "${source_dir}/compose.yaml" "${install_root}/compose.yaml"
install -o root -g root -m 0755 \
    "${source_dir}/backup_uptime_kuma.py" \
    "${library_root}/backup_uptime_kuma.py"
install -o root -g root -m 0644 \
    "${source_dir}/uptime-kuma-backup.service" \
    /etc/systemd/system/uptime-kuma-backup.service
install -o root -g root -m 0644 \
    "${source_dir}/uptime-kuma-backup.timer" \
    /etc/systemd/system/uptime-kuma-backup.timer

cd "${install_root}"
docker compose config --quiet
docker compose pull
docker compose up -d --remove-orphans

for _ in $(seq 1 90); do
    state=$(docker inspect uptime-kuma --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)
    if [[ ${state} == healthy ]]; then
        break
    fi
    if [[ ${state} == exited || ${state} == dead ]]; then
        docker logs --tail 100 uptime-kuma >&2 || true
        exit 1
    fi
    sleep 2
done
test "$(docker inspect uptime-kuma --format '{{.State.Health.Status}}')" = healthy
curl --fail --silent --show-error http://127.0.0.1:3001/ >/dev/null

tailscale serve --bg --yes --https=10000 http://127.0.0.1:3001
systemctl daemon-reload
systemctl enable --now uptime-kuma-backup.timer

echo "Uptime Kuma is healthy."
echo "Tailnet URL: https://$(tailscale status --self --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"Self\"][\"DNSName\"].rstrip(\".\"))'):10000"
echo "Configuration backup: ${change_backup}"
