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

Recommended monitors:

1. `DSTT Public Readiness`: HTTPS, `https://dstt.dipalette.com/health/ready`,
   120-second interval, 3 retries, accepted status 200-299, certificate expiry.
2. `DSTT Public Login`: HTTPS, `https://dstt.dipalette.com/auth/login`,
   300-second interval, 2 retries.
3. `dipalette Tailscale SSH`: TCP port, host `100.116.90.83`, port 22,
   300-second interval, 2 retries.
4. `DSTT off-site backup`: push monitor updated only after backup verification.

The public readiness monitor exercises DNS, Cloudflare, TLS, Nginx, Flask, and
the database. The Tailscale monitor distinguishes an application/edge failure
from a complete host or connectivity failure.

## Operations

```bash
sudo docker compose -f /opt/dstt-monitoring/compose.yaml ps
sudo docker compose -f /opt/dstt-monitoring/compose.yaml logs --tail 100
sudo systemctl status uptime-kuma-backup.timer
sudo systemctl start uptime-kuma-backup.service
sudo tailscale serve status
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
5. Open the dashboard and verify monitors and notifications.
6. Move the Tailscale Serve endpoint or update the dashboard bookmark.

Do not copy a live `kuma.db`, WAL, and SHM independently. Restore from a
completed verified snapshot.
