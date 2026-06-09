"""ShiftPlanningResult を CloudShift の下書き entry 形式へ変換する反映層。

設計書「CloudShift への反映設計 / entry 変換ルール」に対応する。
ここでは DB 保存は行わず、`draft_entries_per_day` 形式（{day: [entry, ...]}）の
dict を生成するだけにとどめる。実際の保存は既存の下書き保存処理へ渡す。

主なルール:
- `id` は `engine-<request_id>-<slot_instance_id>` で安定生成（固定既存は元 id を維持）。
- `value` は `!option!氏名`（option 付き）または `氏名`（option 無し）にエンコードし、
  必ず非空にする（空 value は正規化で破棄され件数が崩れるため）。
- 現場リンクは割当先 slot 由来の値で埋める（Assignment が保持済み）。
- `comment` には短い生成理由を入れる。詳細理由は plan レスポンス側で表示する。
"""

from __future__ import annotations

from typing import Any

try:
    from .cloudshift_shift_engine import ShiftPlanningRequest, ShiftPlanningResult
except ImportError:  # pragma: no cover
    from app.services.cloudshift_shift_engine import (  # type: ignore
        ShiftPlanningRequest,
        ShiftPlanningResult,
    )


def _encode_value(option: str, name: str) -> str:
    """entry value を `!option!氏名` 形式（option があれば）でエンコードする。"""
    option = str(option or "").strip()
    name = str(name or "").strip()
    if not name:
        return ""
    if option:
        return f"!{option}!{name}"
    return name


def build_draft_entries(
    request: ShiftPlanningRequest, result: ShiftPlanningResult
) -> dict[str, list[dict[str, Any]]]:
    """result.assignments を `draft_entries_per_day` 形式へ変換する。"""
    # 固定既存は元の entry_id を維持して同一性を保つ
    entry_id_by_key: dict[tuple[str, str, str], str] = {}
    for existing in request.existing_assignments:
        key = (
            str(existing.employee_number or "").strip(),
            existing.date.isoformat(),
            str(existing.shift_key or "").strip(),
        )
        if existing.entry_id:
            entry_id_by_key[key] = existing.entry_id

    # 生成理由（assignment_id -> summary）
    summary_by_assignment = {ex.assignment_id: ex.summary for ex in result.explanations}

    by_day: dict[str, list[dict[str, Any]]] = {}
    for assignment in result.assignments:
        option = str(assignment.shift_key or "").strip()
        name = assignment.employee_name or assignment.employee_number
        value = _encode_value(option, name)
        if not value:
            # 空 value は正規化で破棄されるため配置をスキップ（件数を崩さない）
            continue

        key = (
            str(assignment.employee_number or "").strip(),
            assignment.date.isoformat(),
            option,
        )
        entry_id = ""
        if assignment.source == "existing_locked":
            entry_id = entry_id_by_key.get(key, "")
        if not entry_id:
            entry_id = f"engine-{request.request_id}-{assignment.slot_instance_id}"

        comment = summary_by_assignment.get(assignment.assignment_id, "")

        entry = {
            "id": entry_id,
            "value": value,
            "comment": comment,
            "employee_name": str(name),
            "employee_number": str(assignment.employee_number or ""),
            "site_row_id": str(assignment.site_row_id or ""),
            "site_id": str(assignment.site_id or ""),
            "site_name": str(assignment.site_name or ""),
            "site_branch_row_id": str(assignment.site_branch_row_id or ""),
            "site_branch": str(assignment.site_branch or ""),
            # 生成由来判定は entry id prefix と下書き保存履歴で扱うため空欄
            "sync_source_type": "",
        }
        by_day.setdefault(str(assignment.day), []).append(entry)

    # 日内の並びを決定的にする（時刻 option → 氏名 → id）
    for entries in by_day.values():
        entries.sort(key=lambda e: (str(e.get("value") or ""), str(e.get("id") or "")))
    return by_day


def build_draft_payload(
    request: ShiftPlanningRequest, result: ShiftPlanningResult
) -> dict[str, Any]:
    """既存の下書き保存処理に渡す payload（{entries_per_day: {...}}）を作る。"""
    return {"entries_per_day": build_draft_entries(request, result)}


def preview_entry_count(draft_entries: dict[str, list[dict[str, Any]]]) -> int:
    """生成された下書き entry の総数を返す（プレビュー件数チェック用）。"""
    return sum(len(entries) for entries in draft_entries.values() if isinstance(entries, list))
