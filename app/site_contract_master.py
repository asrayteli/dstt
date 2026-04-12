from __future__ import annotations

from app.models import Site, SiteBranch, SiteContractMaster, db


VALID_SEGMENTS = {"役員", "一般", "旅客"}


def _normalize_site_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.zfill(5)


def _normalize_site_branch(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.zfill(3)


def compose_contract_code(site_id, site_branch) -> str:
    return f"{_normalize_site_id(site_id)}{_normalize_site_branch(site_branch)}"


def site_manager_name(site: Site) -> str:
    return f"{site.site_manager_last} {site.site_manager_first}".strip()


def normalize_manager_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def manager_ids_match(left, right) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    return left_text == right_text or normalize_manager_id(left_text) == normalize_manager_id(right_text)


def sync_contract_master() -> dict[str, int]:
    summary = {"created": 0, "updated": 0, "deactivated": 0}
    existing_rows = {
        str(row.contract_code or ""): row
        for row in SiteContractMaster.query.order_by(SiteContractMaster.contract_code.asc()).all()
    }
    matched_contract_codes: set[str] = set()

    sites = Site.query.order_by(Site.id.asc()).all()
    for site in sites:
        manager_name = site_manager_name(site)
        for branch in site.branches:
            contract_code = compose_contract_code(site.site_id, branch.site_branch)
            row = existing_rows.get(contract_code)
            if row is None:
                row = SiteContractMaster(contract_code=contract_code)
                db.session.add(row)
                existing_rows[contract_code] = row
                summary["created"] += 1
            else:
                summary["updated"] += 1

            row.contract_code = contract_code
            row.site_row_id = site.id
            row.site_branch_row_id = branch.id
            row.site_id = site.site_id
            row.site_branch = branch.site_branch
            row.site_name = site.site_name
            row.site_manager_id = site.site_manager_id
            row.site_manager_name = manager_name
            row.cloudshift_option_key = branch.cloudshift_option_key
            row.is_active = bool(site.is_active and branch.is_active)
            if not row.source:
                row.source = "siteplus"

            matched_contract_codes.add(contract_code)

    for contract_code, row in existing_rows.items():
        if contract_code in matched_contract_codes:
            continue
        if row.is_active:
            row.is_active = False
            summary["deactivated"] += 1

    return summary


def ensure_contract_master_synced() -> dict[str, int] | None:
    branch_count = SiteBranch.query.count()
    master_count = SiteContractMaster.query.count()
    if branch_count == 0:
        return None
    if master_count >= branch_count:
        return None
    summary = sync_contract_master()
    db.session.commit()
    return summary


def load_site_mapping(*, site_manager_id: str | None = None) -> tuple[dict[str, str], list[str], int]:
    ensure_contract_master_synced()

    rows = (
        SiteContractMaster.query
        .filter(SiteContractMaster.is_active.is_(True))
        .order_by(SiteContractMaster.contract_code.asc())
        .all()
    )

    manager_id = str(site_manager_id or "").strip()
    if manager_id:
        rows = [row for row in rows if manager_ids_match(row.site_manager_id, manager_id)]

    site_dict: dict[str, str] = {}
    warnings: list[str] = []
    matched_rows = 0
    for row in rows:
        matched_rows += 1
        contract_code = str(row.contract_code or "").strip()
        segment = str(row.segment or "").strip()
        if not contract_code:
            continue
        if segment not in VALID_SEGMENTS:
            warnings.append(f"セグメント未設定: {contract_code} - {row.site_name}")
            continue
        site_dict[contract_code] = segment

    return site_dict, warnings, matched_rows
