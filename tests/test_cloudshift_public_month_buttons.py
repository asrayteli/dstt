"""ViewPWA/編集URL（公開ページ）のアクションボタンが、実際に使われる
インラインスクリプト（_cloudshift_script.html）で配線されていることを保証する回帰テスト。

公開ページ（cloudshift_public.html）は _cloudshift_script.html を読み込んで動作する。
過去に旧 static/cloudshift/js/cloudshift.js からインラインスクリプトへ移行した際、
編集URLの「前月を追加」「翌月を追加」ボタンの click ハンドラが移植されず、
ボタンは表示されるのにクリックしても無反応、という不具合があった。
ここではテンプレートに存在する公開ページ用ボタンが、必ずスクリプト側で参照
（= addEventListener で配線）されていることを静的に検証する。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "templates" / "_cloudshift_script.html"
PUBLIC_TEMPLATE = ROOT / "app" / "templates" / "cloudshift_public.html"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _public_template_text() -> str:
    return PUBLIC_TEMPLATE.read_text(encoding="utf-8")


def test_public_shared_view_does_not_offer_month_creation_buttons():
    """共有先（編集URLを含む）から新しい月を追加できない。"""
    template = _public_template_text()

    for button_id in ("cloud-add-prev-month", "cloud-add-next-month", "cloud-create-first-month"):
        assert f'id="{button_id}"' not in template


def test_public_interactive_elements_have_handlers_or_form_binding():
    """公開ページの type=button 要素は、すべてスクリプトから参照されている。

    （name 属性を持つフォーム入力はサーバー送信されるため対象外）
    """
    template = _public_template_text()
    script = _script_text()

    # type="button" を持つ要素の id を抽出（フォーム送信されない純粋なボタン）。
    button_ids = re.findall(
        r'<button\b[^>]*\bid="([A-Za-z0-9_-]+)"[^>]*type="button"', template
    )
    button_ids += re.findall(
        r'<button\b[^>]*type="button"[^>]*\bid="([A-Za-z0-9_-]+)"', template
    )

    unwired = [
        button_id
        for button_id in set(button_ids)
        if f"'{button_id}'" not in script and f'"{button_id}"' not in script
    ]
    assert not unwired, f"公開ページのボタンがスクリプトで配線されていない: {unwired}"


def test_bulk_copy_paths_filter_synced_and_substitute_entries():
    """一括コピー（範囲コピー・単日コピー）の両方が、同期ミラーや代務要請の
    表示専用エントリを複製対象から除外していること。

    範囲コピーだけがフィルタし、単日コピーがフィルタしないと、読み取り専用の
    ミラーや申請プレースホルダを実体エントリとして複製してしまう不具合になる。
    両経路で共通ヘルパー bulkCopyableSourceEntries を経由することで担保する。
    """
    script = _script_text()

    # 共通フィルタヘルパーが定義され、複数のコピー経路から使われていること。
    assert "function bulkCopyableSourceEntries(" in script
    # 定義1 + 範囲コピー + 単日コピー = 最低3回出現する。
    assert script.count("bulkCopyableSourceEntries(") >= 3

    # 単日コピーが、生の sourceEntries を無条件に map する旧実装に戻っていないこと。
    assert (
        "const sourceEntries = bulkCopyableSourceEntries(currentEntries[String(sourceDay)])"
        in script
    )
