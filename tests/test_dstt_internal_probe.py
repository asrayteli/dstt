import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "monitoring"
    / "asrayhome"
    / "dstt_internal_probe.py"
)
SPEC = importlib.util.spec_from_file_location("dstt_internal_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(probe)

AUDIT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "monitoring"
    / "asrayhome"
    / "dstt_daily_audit.py"
)
AUDIT_SPEC = importlib.util.spec_from_file_location("dstt_daily_audit", AUDIT_SCRIPT)
audit = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC and AUDIT_SPEC.loader
AUDIT_SPEC.loader.exec_module(audit)


def test_healthy_probe_is_accepted():
    values = probe.parse_probe_output(
        "dstt=active\nnginx=active\nready=200\nfailed=0\ndisk=27\nmem_kib=2097152\nroot_rw=yes\n"
    )

    healthy, message = probe.evaluate_probe(values)

    assert healthy is True
    assert "disk=27%" in message


def test_unhealthy_probe_reports_every_failed_guard():
    values = probe.parse_probe_output(
        "dstt=failed\nnginx=active\nready=503\nfailed=2\ndisk=91\nmem_kib=131072\nroot_rw=no\n"
    )

    healthy, message = probe.evaluate_probe(values)

    assert healthy is False
    assert "dstt=failed" in message
    assert "ready=503" in message
    assert "failed_units=2" in message
    assert "disk=91%" in message
    assert "available_memory=128MiB" in message
    assert "root_filesystem=read-only" in message


def test_daily_audit_sections_parse_current_report_format():
    report = "===SVC_DSTT===\nactive\n===HTTP===\n200\n===END===\n"

    assert audit.section("SVC_DSTT", report) == "active"
    assert audit.section("HTTP", report) == "200"
    assert audit.section("MISSING", report) == "N/A"
