#!/usr/bin/env python3
"""Send the existing daily DSTT report using the hardened runtime layout."""

from __future__ import annotations

import datetime as dt
from email.mime.text import MIMEText
from email.utils import formataddr
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess


REMOTE_COMMANDS = r"""
echo '===SVC_DSTT==='
systemctl is-active dstt.service 2>/dev/null || true
echo '===SVC_NGINX==='
systemctl is-active nginx.service 2>/dev/null || true
echo '===HTTP==='
curl --silent --output /dev/null --write-out '%{http_code}' --max-time 15 http://127.0.0.1:5000/health/ready || true
echo
echo '===ERR_COUNT==='
journalctl -u dstt.service --since '24 hours ago' --no-pager -p err 2>/dev/null | grep -vc '^-- No entries --$' || true
echo '===ERR_LOG==='
journalctl -u dstt.service --since '24 hours ago' --no-pager -p err 2>/dev/null || true
echo '===MEM==='
free -h | awk '/^Mem:/ {print}'
echo '===DISK==='
df -h / | tail -n 1
echo '===UPTIME==='
uptime -p
echo '===GIT==='
git -C /home/asray/tools/dstt log --oneline -3 2>/dev/null || true
echo '===DB==='
ls -lh /home/asray/.local/share/dstt/instance/users.db 2>/dev/null || true
echo '===BACKUP==='
cat /home/asray/backups/dstt/LATEST 2>/dev/null || true
echo '===END==='
"""


def load_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def section(name: str, report: str) -> str:
    match = re.search(rf"==={re.escape(name)}===\s*(.*?)(?=\n===)", report, re.DOTALL)
    return match.group(1).strip() if match else "N/A"


def main() -> None:
    environment = {**load_environment(Path.home() / ".hermes" / ".env"), **os.environ}
    email_address = environment.get("EMAIL_ADDRESS", "").strip()
    email_password = environment.get("EMAIL_PASSWORD", "").strip()
    if not email_address or not email_password:
        raise RuntimeError("EMAIL_ADDRESS and EMAIL_PASSWORD must be configured in ~/.hermes/.env")

    remote_host = environment.get("DSTT_REMOTE_HOST", "home-server")
    completed = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", remote_host],
        input=REMOTE_COMMANDS,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    if completed.returncode != 0:
        remote_output = f"===END===\nSSH failed: {completed.stderr.strip()}"
    else:
        remote_output = completed.stdout

    dstt = section("SVC_DSTT", remote_output)
    nginx = section("SVC_NGINX", remote_output)
    http = section("HTTP", remote_output)
    errors = section("ERR_COUNT", remote_output)
    error_log = section("ERR_LOG", remote_output)
    memory = section("MEM", remote_output).replace("\n", " / ")
    disk = section("DISK", remote_output)
    uptime = section("UPTIME", remote_output)
    git_log = section("GIT", remote_output)
    database = section("DB", remote_output)
    backup = section("BACKUP", remote_output)
    now = dt.datetime.now().astimezone()

    healthy = dstt == "active" and nginx == "active" and http == "200" and errors in {"0", "N/A"}
    body = f"""DSTT システム監査レポート
実施日時: {now:%Y/%m/%d %H:%M %Z}

サービス
  DSTT: {dstt}
  Nginx: {nginx}
  DB readiness HTTP: {http}
  総合: {'正常' if healthy else '要確認'}

直近24時間のエラー: {errors}件
{error_log if errors not in {'0', 'N/A'} else 'エラー記録なし'}

リソース
  メモリ: {memory}
  ディスク: {disk}
  稼働時間: {uptime}

デプロイ
{git_log}

DB: {database}
本番バックアップ: {backup}

リアルタイム監視: Uptime Kuma
https://asrayhome-server.tail6e21fe.ts.net:10000/
"""

    recipient = environment.get("DSTT_AUDIT_TO", "katsuki_honaga@shidax.co.jp")
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = f"{'✅' if healthy else '⚠️'} DSTT 日次監査 ({now:%Y/%m/%d})"
    message["From"] = formataddr(("DSTT Monitoring", email_address))
    message["To"] = recipient

    smtp_host = environment.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(environment.get("EMAIL_SMTP_PORT", "587"))
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(email_address, email_password)
        server.send_message(message)
    print(f"daily audit sent: healthy={healthy} recipient={recipient}")


if __name__ == "__main__":
    main()
