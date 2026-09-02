"""ツール間API（tool-to-tool API）の宣言的レジストリ。

背景
----
ShifterSync / CloudShift・有休共有ツール・ToBell は、社員名簿PLUS（pluslist）や
現場リストPLUS（siteplus）が持つマスタを「候補検索」などの用途で参照する。
従来はこれらの参照も提供元ツールの Blueprint ガードを素通りできないため、
利用者に **提供元ツールそのもののアクセス権** を与える必要があった。
しかしそれは名簿全体（住所・賃金・編集履歴など）の閲覧許可でもあるため、
「ShifterSync を使わせたいだけ」なのに過剰な権限を配る結果になっていた。

そこでツール間APIを明示的に列挙し、``UserToolPermission.scope='api'``
（＝ツール間API専用許可）だけを持つ利用者にも、ここに載っている経路に限って
参照を許す。判定は :mod:`app.access_control` 側で行う。

設計上の約束
------------
* **提供元（provider）** … データを持つ側のツールキー（例: ``pluslist``）。
* **利用側（consumer）** … そのデータを使う側のツールキー（例: ``shiftersync``）。
  API専用許可が効くのは「利用側ツールの通常アクセス権を持っている」場合だけ。
  API専用許可だけでは何も参照できない（利用側の権限との AND 条件）。
* **項目（fields）** … API専用許可で返してよいレスポンス項目。``None`` は
  項目制限なし（個人情報を含まないマスタなど）。提供元ツールの通常アクセス権を
  持つ利用者には従来どおり全項目を返す。
* ここに載せてよいのは **参照専用（GET/HEAD）** かつ **提供元側で行レベルの
  絞り込み（営業所スコープ）を済ませている** エンドポイントだけ。
  更新系や絞り込みの無いエンドポイントは絶対に載せない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# ツール間API専用許可（scope='api'）で使える scope 値。
TOOL_SCOPE_FULL = "full"
TOOL_SCOPE_API = "api"
TOOL_SCOPES = (TOOL_SCOPE_FULL, TOOL_SCOPE_API)


# 社員名簿PLUS: API専用許可で渡してよい項目（本人特定に必要な最小限）。
# 住所・郵便番号・マンション名（＝封筒印刷用）は含めない。これらを使う
# PowerStamp の宛名差し込みは、従来どおり社員名簿PLUSの通常許可を必要とする。
PLUSLIST_IDENTITY_FIELDS = frozenset(
    {"employee_number", "employee_name", "office_name", "job_title"}
)


# 提供元ツール → {利用側ツール: API専用許可で渡してよい項目}
#
# ここに利用側を足すことは「その利用側ツールの権限があれば、提供元のAPI専用許可で
# データに到達できる」という意味になる。公開（public）ツールを足すと実質
# 「API専用許可だけで到達できる」ことになるため、追加は慎重に。
TOOL_API_CONSUMERS: dict[str, dict[str, frozenset[str] | None]] = {
    "pluslist": {
        # ShifterSync / CloudShift の社員候補サーチ
        "shiftersync": PLUSLIST_IDENTITY_FIELDS,
        # 有休共有ツールの社員名入力補助
        "leave_mgr": PLUSLIST_IDENTITY_FIELDS,
        # ToBell のプロジェクト/タスク⇄社員の紐付け（ToBell は公開ツールだが、
        # 紐付け機能自体が個人トグルのオプトイン制で、返す項目も識別情報のみ）
        "to_bell": PLUSLIST_IDENTITY_FIELDS,
    },
    "siteplus": {
        # CloudShift の現場・枝番号サーチ（現場マスタは個人情報を含まない）
        "shiftersync": None,
        # ToBell のプロジェクト/タスク⇄現場の紐付け
        "to_bell": None,
    },
}


@dataclass(frozen=True)
class ToolApiEndpoint:
    """提供元ツールの Blueprint 上で公開されるツール間APIエンドポイント。

    ``endpoint``（Flask のエンドポイント名 = ``<blueprint>.<関数名>``）をキーに登録する。
    URL ではなくエンドポイント名で持つのは、URL 変更で許可がずれないようにするため。
    登録名が実在しなくなった場合は「許可されない（＝従来どおり提供元ツールの権限が必要）」
    側に倒れる。
    """

    provider: str
    label: str
    # このエンドポイントを呼ぶ利用側ツール。TOOL_API_CONSUMERS[provider] の部分集合。
    consumers: tuple[str, ...]
    # 許可する HTTP メソッド。参照専用のみ。
    methods: frozenset[str] = frozenset({"GET", "HEAD"})


TOOL_API_ENDPOINTS: dict[str, ToolApiEndpoint] = {
    "pluslist.search_employee_api": ToolApiEndpoint(
        provider="pluslist",
        label="社員候補サーチ",
        consumers=("shiftersync", "leave_mgr"),
    ),
    "siteplus.api_cloudshift_sites": ToolApiEndpoint(
        provider="siteplus",
        label="現場候補サーチ",
        consumers=("shiftersync",),
    ),
    "siteplus.api_cloudshift_branches": ToolApiEndpoint(
        provider="siteplus",
        label="枝番号サーチ",
        consumers=("shiftersync",),
    ),
}


def normalize_tool_scope(value) -> str:
    """DB 上の scope 値を正規化する。

    ``'api'`` 以外（NULL・空・未知の値）はすべて ``'full'`` とみなす。
    scope 追加前に作られた行は NULL/未設定になり得るが、それらは
    「従来どおりの通常許可」であって API 専用許可ではないため。
    書き込み側（:func:`app.access_control.set_tool_access_scopes`）で
    ``TOOL_SCOPES`` 以外の値は弾く。
    """
    text = str(value or "").strip().lower()
    return TOOL_SCOPE_API if text == TOOL_SCOPE_API else TOOL_SCOPE_FULL


def api_provider_tool_keys() -> frozenset[str]:
    """ツール間API専用許可を付与できる提供元ツールキー。"""
    return frozenset(TOOL_API_CONSUMERS)


def api_consumers_for(provider: str) -> Mapping[str, frozenset[str] | None]:
    """提供元ツールを参照してよい利用側ツールと、その項目制限。"""
    return TOOL_API_CONSUMERS.get(provider, {})


def api_endpoints_for(provider: str) -> dict[str, ToolApiEndpoint]:
    return {
        name: spec
        for name, spec in TOOL_API_ENDPOINTS.items()
        if spec.provider == provider
    }


def endpoint_problems(name: str, spec: ToolApiEndpoint) -> list[str]:
    """1件のエンドポイント登録の整合性チェック。空リストなら健全。"""
    problems: list[str] = []
    if "." not in name:
        problems.append(f"{name}: エンドポイント名は '<blueprint>.<関数名>' 形式で登録する")
    elif name.split(".", 1)[0] != spec.provider:
        problems.append(f"{name}: Blueprint 名と provider ({spec.provider}) が一致しない")
    if spec.provider not in TOOL_API_CONSUMERS:
        problems.append(f"{name}: provider {spec.provider} が TOOL_API_CONSUMERS に無い")
        return problems
    if not spec.consumers:
        problems.append(f"{name}: consumers が空（誰も呼べない登録）")
    allowed = TOOL_API_CONSUMERS[spec.provider]
    for consumer in spec.consumers:
        if consumer not in allowed:
            problems.append(
                f"{name}: consumer {consumer} が TOOL_API_CONSUMERS[{spec.provider}] に無い"
            )
    if not spec.methods:
        problems.append(f"{name}: methods が空")
    unsafe = {m for m in spec.methods if m.upper() not in {"GET", "HEAD"}}
    if unsafe:
        problems.append(f"{name}: 参照専用でないメソッドは登録できない: {sorted(unsafe)}")
    return problems


def registry_problems() -> list[str]:
    """レジストリ自身の整合性チェック（起動時ログ／テスト用）。"""
    problems: list[str] = []
    for name, spec in TOOL_API_ENDPOINTS.items():
        problems.extend(endpoint_problems(name, spec))
    return problems


# 整合性チェックを通ったエンドポイントだけを許可判定に使う。宣言ミスは
# 「許可されない（＝従来どおり提供元ツールの通常許可が必要）」側へ倒す。
VALID_TOOL_API_ENDPOINTS: dict[str, ToolApiEndpoint] = {
    name: spec
    for name, spec in TOOL_API_ENDPOINTS.items()
    if not endpoint_problems(name, spec)
}
