#!/usr/bin/env python3
"""Probe dipalette from AsrayHome and report the result to Uptime Kuma."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request


REMOTE_SCRIPT = r"""
printf 'dstt=%s\n' "$(systemctl is-active dstt.service 2>/dev/null || true)"
printf 'nginx=%s\n' "$(systemctl is-active nginx.service 2>/dev/null || true)"
printf 'ready=%s\n' "$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 http://127.0.0.1:5000/health/ready || true)"
printf 'failed=%s\n' "$(systemctl --failed --no-legend --plain 2>/dev/null | grep -c . || true)"
printf 'disk=%s\n' "$(df -P / | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')"
printf 'mem_kib=%s\n' "$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)"
printf 'root_rw=%s\n' "$(findmnt -no OPTIONS / | tr ',' '\n' | grep -qx rw && echo yes || echo no)"
"""


def parse_probe_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            result[key.strip()] = value.strip()
    return result


def evaluate_probe(values: dict[str, str]) -> tuple[bool, str]:
    failures: list[str] = []
    if values.get("dstt") != "active":
        failures.append(f"dstt={values.get('dstt', 'unknown')}")
    if values.get("nginx") != "active":
        failures.append(f"nginx={values.get('nginx', 'unknown')}")
    if values.get("ready") != "200":
        failures.append(f"ready={values.get('ready', 'unknown')}")
    try:
        if int(values.get("failed", "-1")) != 0:
            failures.append(f"failed_units={values.get('failed', 'unknown')}")
    except ValueError:
        failures.append("failed_units=invalid")
    try:
        disk = int(values.get("disk", "101"))
        if disk >= 85:
            failures.append(f"disk={disk}%")
    except ValueError:
        failures.append("disk=invalid")
    try:
        available_mib = int(values.get("mem_kib", "0")) // 1024
        if available_mib < 512:
            failures.append(f"available_memory={available_mib}MiB")
    except ValueError:
        failures.append("available_memory=invalid")
    if values.get("root_rw") != "yes":
        failures.append("root_filesystem=read-only")

    if failures:
        return False, "; ".join(failures)
    return True, f"services=active; ready=200; disk={values['disk']}%; mem_available={int(values['mem_kib']) // 1024}MiB"


def send_heartbeat(push_url: str, *, healthy: bool, message: str, elapsed_ms: int) -> None:
    query = urllib.parse.urlencode(
        {
            "status": "up" if healthy else "down",
            "msg": message[:240],
            "ping": max(0, elapsed_ms),
        }
    )
    separator = "&" if "?" in push_url else "?"
    request = urllib.request.Request(f"{push_url}{separator}{query}", method="GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status // 100 != 2:
            raise RuntimeError(f"Uptime Kuma returned HTTP {response.status}")


def main() -> int:
    push_url = os.environ.get("DSTT_INTERNAL_PUSH_URL", "").strip()
    if not push_url:
        print("DSTT_INTERNAL_PUSH_URL is not configured", file=sys.stderr)
        return 2

    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "home-server"],
            input=REMOTE_SCRIPT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "SSH failed"
            healthy, message = False, f"dipalette SSH probe failed: {detail}"
        else:
            healthy, message = evaluate_probe(parse_probe_output(completed.stdout))
    except (OSError, subprocess.TimeoutExpired) as error:
        healthy, message = False, f"dipalette probe error: {error}"

    elapsed_ms = round((time.monotonic() - started) * 1000)
    send_heartbeat(push_url, healthy=healthy, message=message, elapsed_ms=elapsed_ms)
    print(f"status={'up' if healthy else 'down'} ping={elapsed_ms}ms {message}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
