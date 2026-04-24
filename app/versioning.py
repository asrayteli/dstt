from __future__ import annotations

from pathlib import Path
import re
import subprocess


_MAJOR_PATTERNS = (
    r"^feat(\(.+\))?:",
    r"\badd(ed|s|ing)?\b",
    r"追加",
    r"新規",
    r"導入",
    r"拡張",
    r"redesign",
    r"overhaul",
    r"introduce",
    r"extend",
)

_MINOR_PATTERNS = (
    r"\bimprove(d|s|ment)?\b",
    r"\bupdate(d|s)?\b",
    r"\brefactor(ed|s|ing)?\b",
    r"streamline",
    r"unify",
    r"統一",
    r"\bui\b",
    r"\bux\b",
    r"display",
    r"show",
    r"enhance",
)

_PATCH_PATTERNS = (
    r"^fix(\(.+\))?:",
    r"\bfix(ed|es|ing)?\b",
    r"\bbug\b",
    r"修正",
    r"hotfix",
    r"harden",
    r"security",
)


def _classify_commit(message: str) -> str | None:
    text = message.strip().lower()
    if not text or text.startswith("merge pull request"):
        return None

    if any(re.search(pattern, text) for pattern in _MAJOR_PATTERNS):
        return "major"
    if any(re.search(pattern, text) for pattern in _PATCH_PATTERNS):
        return "patch"
    if any(re.search(pattern, text) for pattern in _MINOR_PATTERNS):
        return "minor"
    return None


def calculate_repo_version(repo_root: Path, base_version: str = "1.0.0") -> str:
    major, minor, patch = (int(part) for part in base_version.split("."))
    try:
        output = subprocess.check_output(
            ["git", "log", "--pretty=%s", "--reverse"],
            cwd=str(repo_root),
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        return f"v{base_version}"

    for message in output.splitlines():
        level = _classify_commit(message)
        if level == "major":
            major += 1
            minor = 0
            patch = 0
        elif level == "minor":
            minor += 1
            patch = 0
        elif level == "patch":
            patch += 1

    return f"v{major}.{minor}.{patch}"
