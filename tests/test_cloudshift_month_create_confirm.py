"""月の追加が「前月コピー / 空で作成」の二択ではなく、単純な確認になっていること。

個人・現場・大規模・要代務のいずれも、月の作成はインラインスクリプトの
``createMonth()`` 1本を通る（画面上のボタンはモード共通）。一括編集とテンプレートが
揃った現在、前月コピーは使わない方針になったため、常に空の月を作る。
過去に window.confirm の OK/キャンセルへコピー可否を割り当てていた名残が
戻らないよう、静的に検証する。
"""

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "app" / "templates" / "_cloudshift_script.html"


def _create_month_source() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("async function createMonth(")
    end = text.index("async function navigateMonth(", start)
    return text[start:end]


def test_create_month_always_requests_a_blank_month():
    source = _create_month_source()
    assert "init_mode: 'blank'" in source
    assert "'copy'" not in source
    assert "source_month_key: null" in source


def test_create_month_asks_a_plain_confirmation():
    source = _create_month_source()
    # 既存月がある状態からの追加では「よろしいですか？」の確認を出す。
    assert "を追加します。よろしいですか？" in source
    assert re.search(r"cloudConfirm\(\{[\s\S]{0,300}?を追加します。よろしいですか？", source)
    # ブラウザ標準の confirm でコピー可否を尋ねる旧実装が戻っていないこと。
    assert "window.confirm" not in source
    assert "コピーして作成しますか" not in source


def test_server_still_defaults_to_blank_month():
    """サーバー側の既定も blank のままであること（クライアントと矛盾させない）。"""
    source = (ROOT / "app" / "tools" / "cloudshift.py").read_text(encoding="utf-8")
    assert 'init_mode = (payload.get("init_mode") or "blank").strip().lower()' in source
