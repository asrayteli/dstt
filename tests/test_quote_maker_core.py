"""見積書作成ツールの純粋ロジック（QM_CORE）を Node で単体テストする。

``app/templates/quote_maker.html`` の ``QM_CORE_BEGIN``〜``QM_CORE_END`` を
抜き出し、``tests/quote_maker_core.test.js`` で検証する。項目表の行列操作と
セル結合、差出人の可変項目、2段組の入れ子探索といった画面に依存しない部分は
ここで壊れていないことを確認する。Node が無い環境ではスキップする。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "quote_maker.html"
RUNNER = Path(__file__).resolve().parent / "quote_maker_core.test.js"

_CORE_RE = re.compile(r"/\* QM_CORE_BEGIN \*/(.*?)/\* QM_CORE_END \*/", re.S)


def _extract_core() -> str:
    source = TEMPLATE.read_text(encoding="utf-8")
    matches = _CORE_RE.findall(source)
    assert len(matches) == 1, "QM_CORE ブロックはテンプレートにちょうど1つ必要です"
    return matches[0]


def test_core_block_is_dom_free():
    """QM_CORE は DOM/ブラウザAPIに触れない（Node でそのまま実行できる）。"""
    core = _extract_core()
    for forbidden in ("document.", "localStorage", "indexedDB", "fetch(", "alert("):
        assert forbidden not in core, f"QM_CORE に {forbidden} を書かないでください"
    # Jinja のテンプレート構文が混ざると Node で実行できなくなる
    assert "{{" not in core and "{%" not in core


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js が無い環境ではスキップ")
def test_core_logic_unit_tests(tmp_path):
    core_js = tmp_path / "qm_core.js"
    core_js.write_text(_extract_core(), encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(RUNNER), str(core_js)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, "QM_CORE の単体テストが失敗しました"
