# DSTT monitoring runbook

## Architecture

AsrayHome is a separate-site monitoring and backup hub. Uptime Kuma runs as a
replaceable Docker container. Configuration and history are held only in the
bind-mounted data directory, independently of Docker and the Compose file.

- Compose configuration: `/opt/dstt-monitoring/compose.yaml`
- Persistent data: `/var/lib/uptime-kuma`
- Verified backups: `/var/backups/uptime-kuma` (14 generations)
- Backup implementation: `/usr/local/lib/dstt-monitoring/backup_uptime_kuma.py`
- Tailnet dashboard: `https://asrayhome-server.tail6e21fe.ts.net:10000`
- Container listener: `127.0.0.1:3001` only

Uptime Kuma data must stay on a local filesystem. Do not put `/app/data` on
NFS, SMB, or another filesystem without reliable POSIX locks.

## Initial installation

On AsrayHome, from a checkout containing this directory:

```bash
sudo bash deploy/monitoring/uptime-kuma/install_asrayhome.sh
```

Open the tailnet dashboard, create the first administrator, enable two-factor
authentication, and store the recovery material in a password manager.
The backup service intentionally skips runs until the initial SQLite database
setup has created `/var/lib/uptime-kuma/kuma.db`.

Managed monitors:

1. `DSTT Public Readiness`: HTTPS, `https://dstt.dipalette.com/health/ready`,
   120-second interval, 3 retries, accepted status 200-299, certificate expiry.
2. `DSTT Public Login`: HTTPS, `https://dstt.dipalette.com/auth/login`,
   300-second interval, 2 retries.
3. `DSTT Public DNS`: DNS A lookup through `1.1.1.1`, 300-second interval.
4. `dipalette Tailscale Ping`: ICMP over the Tailnet, 300-second interval.
5. `dipalette Tailscale SSH`: TCP port, host `100.116.90.83`, port 22,
   300-second interval, 2 retries.
6. `dipalette internal health`: five-minute push monitor checking the DSTT and
   Nginx services, local database readiness, failed systemd units, disk space,
   available memory, and a writable root filesystem.
7. `DSTT off-site backup`: 26-hour push monitor updated only after a verified
   backup is present on AsrayHome.

The public readiness monitor exercises DNS, Cloudflare, TLS, Nginx, Flask, and
the database. The Tailscale monitor distinguishes an application/edge failure
from a complete host or connectivity failure.

## Provisioning DSTT monitors and notifications

`provision_dstt.js` uses Uptime Kuma's authenticated socket API and derives a
short-lived login token from the already initialized local database. It does
not require an administrator password. Run it inside the Uptime Kuma container
with SMTP values supplied as environment variables. The operation is
idempotent: missing monitors are created and existing managed monitors are
returned to the declared settings.

The current deployment uses Gmail SMTP and applies `DSTT 障害メール` to every
managed child monitor. Keep the SMTP credential outside Git. A protected push
URL file is generated at `/var/lib/uptime-kuma/dstt-monitor-push.env`.

Install the AsrayHome heartbeat services after provisioning:

```bash
sudo bash deploy/monitoring/asrayhome/install_dstt_integrations.sh
```

This installs and enables:

- `dstt-internal-health.timer`, every five minutes;
- `dstt-backup-offsite.timer`, daily around 04:15 JST;
- the corrected daily audit script, when Hermes is installed.

The integration installer copies push URLs to
`~/.config/dstt-monitoring/push.env` with mode `0600`. Copy this protected file
or re-run provisioning when rebuilding the monitoring host.

## Operations

```bash
sudo docker compose -f /opt/dstt-monitoring/compose.yaml ps
sudo docker compose -f /opt/dstt-monitoring/compose.yaml logs --tail 100
sudo systemctl status uptime-kuma-backup.timer
sudo systemctl start uptime-kuma-backup.service
sudo tailscale serve status
systemctl --user status dstt-internal-health.timer
systemctl --user status dstt-backup-offsite.timer
systemctl --user start dstt-internal-health.service
systemctl --user start dstt-backup-offsite.service
```

Verify the latest monitoring backup:

```bash
latest=$(sudo cat /var/backups/uptime-kuma/LATEST)
sudo python3 /usr/local/lib/dstt-monitoring/backup_uptime_kuma.py \
  --verify-only "/var/backups/uptime-kuma/$latest"
```

## Controlled update

The Uptime Kuma container is deliberately excluded from Watchtower. Update the
image tag in `compose.yaml`, review the upstream release notes, create a backup,
then recreate the container:

```bash
sudo systemctl start uptime-kuma-backup.service
cd /opt/dstt-monitoring
sudo docker compose pull
sudo docker compose up -d --force-recreate
```

## Migration to another monitoring host

1. Install Docker Compose and Tailscale on the new host.
2. Copy `compose.yaml` and the latest verified snapshot.
3. Restore the snapshot's `data` directory as `/var/lib/uptime-kuma`.
4. Run the installer and confirm the container reports healthy.
5. Run `install_dstt_integrations.sh` to restore the push integrations.
6. Open the dashboard and verify monitors and notifications.
7. Send a notification test and manually run both heartbeat services.
8. Move the Tailscale Serve endpoint or update the dashboard bookmark.

Do not copy a live `kuma.db`, WAL, and SHM independently. Restore from a
completed verified snapshot.
