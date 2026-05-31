"""ToBell プロジェクト/タスクと、pluslist 社員 / siteplus 現場 / FILEPOST ファイルの紐付けサービス。"""
from __future__ import annotations

from typing import Any

from app.models import (
    Employee,
    Site,
    SiteBranch,
    ToBellEmployeeLink,
    ToBellExternalFile,
    ToBellSiteLink,
    db,
)


VALID_TARGET_TYPES = {"project", "task"}


def _validate_target(target_type: str) -> str:
    if target_type not in VALID_TARGET_TYPES:
        raise ValueError("target_type は project / task のいずれかにしてください")
    return target_type


# ----- pluslist (社員) -----

def list_employee_links(target_type: str, target_id: int) -> list[dict[str, Any]]:
    _validate_target(target_type)
    rows = ToBellEmployeeLink.query.filter_by(target_type=target_type, target_id=target_id).all()
    employee_ids = [row.employee_id for row in rows]
    if not employee_ids:
        return []
    employees = {e.id: e for e in Employee.query.filter(Employee.id.in_(employee_ids)).all()}
    out: list[dict[str, Any]] = []
    for row in rows:
        emp = employees.get(row.employee_id)
        if emp is None:
            continue
        out.append(
            {
                "link_id": row.id,
                "employee_id": emp.id,
                "employee_number": emp.employee_number,
                "employee_name": emp.employee_name or "",
                "office_name": emp.office_name or "",
                "linked_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out


def add_employee_link(target_type: str, target_id: int, employee_id: int, actor: str) -> ToBellEmployeeLink:
    _validate_target(target_type)
    employee = db.session.get(Employee, int(employee_id))
    if employee is None:
        raise ValueError("社員が見つかりません")
    existing = ToBellEmployeeLink.query.filter_by(
        target_type=target_type, target_id=target_id, employee_id=employee.id
    ).first()
    if existing:
        return existing
    row = ToBellEmployeeLink(
        target_type=target_type,
        target_id=target_id,
        employee_id=employee.id,
        created_by=actor,
    )
    db.session.add(row)
    db.session.commit()
    return row


def get_employee_link(link_id: int) -> ToBellEmployeeLink | None:
    return db.session.get(ToBellEmployeeLink, int(link_id))


def remove_employee_link(link_id: int) -> bool:
    row = db.session.get(ToBellEmployeeLink, int(link_id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def search_employees(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    q = (query or "").strip()
    base = Employee.query.filter(Employee.is_deleted.is_(False) | Employee.is_deleted.is_(None))
    if q:
        like = f"%{q}%"
        base = base.filter(
            db.or_(
                Employee.employee_name.ilike(like),
                Employee.employee_kana.ilike(like),
                Employee.employee_number.ilike(like),
            )
        )
    rows = base.order_by(Employee.employee_number.asc()).limit(limit).all()
    return [
        {
            "employee_id": emp.id,
            "employee_number": emp.employee_number,
            "employee_name": emp.employee_name or "",
            "office_name": emp.office_name or "",
        }
        for emp in rows
    ]


# ----- siteplus (現場) -----

def list_site_links(target_type: str, target_id: int) -> list[dict[str, Any]]:
    _validate_target(target_type)
    rows = ToBellSiteLink.query.filter_by(target_type=target_type, target_id=target_id).all()
    site_ids = [row.site_row_id for row in rows]
    if not site_ids:
        return []
    sites = {s.id: s for s in Site.query.filter(Site.id.in_(site_ids)).all()}
    out: list[dict[str, Any]] = []
    for row in rows:
        site = sites.get(row.site_row_id)
        if site is None:
            continue
        out.append(
            {
                "link_id": row.id,
                "site_row_id": site.id,
                "site_id": site.site_id,
                "site_name": site.site_name or "",
                "linked_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out


def add_site_link(target_type: str, target_id: int, site_row_id: int, actor: str) -> ToBellSiteLink:
    _validate_target(target_type)
    site = db.session.get(Site, int(site_row_id))
    if site is None:
        raise ValueError("現場が見つかりません")
    existing = ToBellSiteLink.query.filter_by(
        target_type=target_type, target_id=target_id, site_row_id=site.id
    ).first()
    if existing:
        return existing
    row = ToBellSiteLink(
        target_type=target_type,
        target_id=target_id,
        site_row_id=site.id,
        created_by=actor,
    )
    db.session.add(row)
    db.session.commit()
    return row


def get_site_link(link_id: int) -> ToBellSiteLink | None:
    return db.session.get(ToBellSiteLink, int(link_id))


def remove_site_link(link_id: int) -> bool:
    row = db.session.get(ToBellSiteLink, int(link_id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def search_sites(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    q = (query or "").strip()
    base = Site.query.filter(Site.is_active.is_(True))
    if q:
        like = f"%{q}%"
        base = base.filter(db.or_(Site.site_name.ilike(like), Site.site_id.ilike(like)))
    rows = base.order_by(Site.site_id.asc()).limit(limit).all()
    return [
        {
            "site_row_id": site.id,
            "site_id": site.site_id,
            "site_name": site.site_name or "",
        }
        for site in rows
    ]


# ----- FILEPOST 外部ファイル -----

def list_external_files(target_type: str, target_id: int) -> list[dict[str, Any]]:
    _validate_target(target_type)
    rows = ToBellExternalFile.query.filter_by(
        target_type=target_type, target_id=target_id
    ).order_by(ToBellExternalFile.created_at.desc()).all()
    return [row.to_dict() for row in rows]


def add_external_file(
    *,
    target_type: str,
    target_id: int,
    share_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    private_url: str,
    uploaded_by: str,
    note: str = "",
) -> ToBellExternalFile:
    _validate_target(target_type)
    row = ToBellExternalFile(
        target_type=target_type,
        target_id=target_id,
        provider="filepost",
        share_id=share_id,
        file_name=file_name[:255],
        file_size=int(file_size or 0),
        mime_type=(mime_type or "")[:120] or None,
        private_url=private_url[:500],
        note=note or "",
        uploaded_by=uploaded_by,
    )
    db.session.add(row)
    db.session.commit()
    return row


def get_external_file(file_id: int) -> ToBellExternalFile | None:
    return db.session.get(ToBellExternalFile, int(file_id))


def remove_external_file(file_id: int) -> bool:
    row = db.session.get(ToBellExternalFile, int(file_id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
