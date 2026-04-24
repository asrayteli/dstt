from pathlib import Path

from app.versioning import calculate_repo_version


def test_calculate_repo_version_format():
    version = calculate_repo_version(Path(__file__).resolve().parents[1])
    assert version.startswith("v")
    parts = version[1:].split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
