# DSTT production runbook

## Supported production layout

- Source checkout: `/home/asray/tools/dstt` (Git; replaceable)
- Python virtual environment: `/home/asray/tools/dstt-ubu` (replaceable)
- Mutable state: `/home/asray/.local/share/dstt` (`DSTT_DATA_DIR`; backed up)
- Local backups: `/home/asray/backups/dstt` (7 verified generations)
- Off-site backups: `asrayhome-server:/home/asray/backups/dstt/dipalette` (30 generations)
- Public traffic: Nginx -> Gunicorn on `127.0.0.1:5000`

Never place a database, key, uploaded document, or generated private file under
`app/static`. Nginx must use `deploy/nginx/dstt.conf`, which sends all requests
through Flask's authorization checks.

## One-time data cut-over

Create a fresh verified backup before this procedure. Stop the service, repeat
the idempotent data migration to capture the final delta, set the environment,
then start it again:

```bash
sudo systemctl stop dstt
/usr/bin/python3 /home/asray/.local/lib/dstt/migrate_runtime_data.py \
  --project-root /home/asray/tools/dstt \
  --data-root /home/asray/.local/share/dstt \
  --database /home/asray/tools/dstt/instance/users.db
# Add DSTT_DATA_DIR=/home/asray/.local/share/dstt to the 0600 EnvironmentFile.
sudo systemctl start dstt
curl --fail https://dstt.dipalette.com/health/ready
```

Keep the old project-local data read-only until at least two off-site backup
runs and one restore verification have succeeded.

## Application deployment

Normal code updates do not require restarting Nginx. After the one-time data
cut-over, use:

```bash
/home/asray/tools/dstt/scripts/deploy_dipalette.sh origin/main
```

The script creates a verified backup, accepts only a fast-forward Git update,
installs pinned dependencies, runs smoke tests, gracefully reloads Gunicorn,
and checks database readiness. Restart or reload Nginx only after a validated
Nginx configuration change (`sudo nginx -t && sudo systemctl reload nginx`).

## PostgreSQL cut-over

Do this separately from a VPS move. Use the SQLite database from a completed
snapshot, not the live WAL database:

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite /home/asray/backups/dstt/SNAPSHOT/database/users.db \
  --preflight-only
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite /home/asray/backups/dstt/SNAPSHOT/database/users.db \
  --target 'postgresql+psycopg://dstt:PASSWORD@127.0.0.1:5432/dstt'
```

The target must be empty. The importer checks SQLite integrity, model/table
parity, string lengths, foreign-key orphans, all row counts, and 59 sequences.
It refuses orphan data unless `--skip-orphans` is explicitly supplied.

## Root-owned hardening tasks

The helper below makes timestamped root-only backups, moves OAuth secrets into
the owner-readable environment file without printing them, installs the
systemd and Nginx configurations, validates both services, and rolls back
automatically if an application or configuration check fails:

```bash
cd /home/asray/tools/dstt
sudo bash scripts/apply_root_hardening.sh
```

Secrets belong only in the owner-readable environment file (`chmod 600`), not
in `/etc/systemd/system/*.conf`, Git, shell history, or deployment output.
