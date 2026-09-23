import json
from datetime import date, datetime, timedelta
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import auth, models as m, planning, services
from app.db import get_db
from app.services import ServiceError
from app.web.common import actor, get_or_404, render, require_user, to_pence

router = APIRouter()


def _int(v: str | None) -> int | None:
    return int(v) if v not in (None, "") else None


# ---------- Contractors ----------

@router.get("/contractors")
def contractors_list(request: Request, q: str = "", db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    query = select(m.Contractor).order_by(m.Contractor.name)
    if q:
        like = f"%{q}%"
        query = query.where(or_(m.Contractor.name.ilike(like), m.Contractor.trades.ilike(like)))
    contractors = db.scalars(query).all()
    return render(request, "contractors.html", user, contractors=contractors, q=q)


@router.post("/contractors")
def contractors_create(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), company: str = Form(""), trades: str = Form(""), phone: str = Form(""),
    email: str = Form(""), website: str = Form(""), recommended_by: str = Form(""),
    registrations: str = Form(""), insurance_checked: str | None = Form(None), rating: str = Form(""),
    notes: str = Form(""),
):
    c = services.create(
        db, actor(user), m.Contractor, name=name, company=company, trades=trades, phone=phone,
        email=email, website=website, recommended_by=recommended_by, registrations=registrations,
        insurance_checked=bool(insurance_checked), rating=_int(rating), notes=notes,
    )
    db.commit()
    return RedirectResponse(f"/contractors/{c.id}", status_code=303)


@router.get("/contractors/{contractor_id}")
def contractor_detail(contractor_id: int, request: Request, db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    c = get_or_404(db, m.Contractor, contractor_id)
    quotes = db.scalars(
        select(m.Quote).where(m.Quote.contractor_id == contractor_id).order_by(m.Quote.received_on.desc())
    ).all()
    items = list({q.item_id: q.item for q in quotes if q.item}.values())
    return render(request, "contractor_detail.html", user, c=c, quotes=quotes, items=items)


@router.post("/contractors/{contractor_id}")
def contractor_update(
    contractor_id: int, db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), company: str = Form(""), trades: str = Form(""), phone: str = Form(""),
    email: str = Form(""), website: str = Form(""), recommended_by: str = Form(""),
    registrations: str = Form(""), insurance_checked: str | None = Form(None), rating: str = Form(""),
    notes: str = Form(""),
):
    c = get_or_404(db, m.Contractor, contractor_id)
    services.update(db, actor(user), c, dict(
        name=name, company=company, trades=trades, phone=phone, email=email, website=website,
        recommended_by=recommended_by, registrations=registrations, insurance_checked=bool(insurance_checked),
        rating=_int(rating), notes=notes,
    ))
    db.commit()
    return RedirectResponse(f"/contractors/{contractor_id}", status_code=303)


# ---------- Timeline ----------

def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


@router.get("/timeline")
def timeline(request: Request, db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    items = db.scalars(
        select(m.Item).where(m.Item.planned_start.isnot(None)).order_by(m.Item.planned_start)
    ).all()
    warnings = planning.timeline_warnings(db)
    groups: dict[str, list[m.Item]] = {}
    for it in items:
        key = it.planned_start.strftime("%Y-%m")
        groups.setdefault(key, []).append(it)
    months = sorted(groups)
    month_labels = {key: date.fromisoformat(f"{key}-01").strftime("%B %Y") for key in months}

    gantt_items = []
    gantt_months = []
    if items:
        range_start = _month_start(min(it.planned_start for it in items))
        range_end = max((it.planned_end or it.planned_start) for it in items)
        range_end = _next_month(_month_start(range_end))
        total_days = (range_end - range_start).days

        cur = range_start
        while cur < range_end:
            gantt_months.append(cur.strftime("%b %Y"))
            cur = _next_month(cur)

        for it in items:
            start = it.planned_start
            end = it.planned_end or (start + timedelta(days=7))
            left = max((start - range_start).days / total_days * 100, 0)
            width = max((end - start).days / total_days * 100, 1.5)
            gantt_items.append({
                "item": it,
                "left": left,
                "width": width,
                "warned": bool(warnings.get(it.id)),
            })

    return render(
        request, "timeline.html", user, months=months, groups=groups, warnings=warnings,
        month_labels=month_labels, gantt_items=gantt_items, gantt_months=gantt_months,
    )


def _ics_escape(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/timeline.ics")
def timeline_ics(db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    items = db.scalars(
        select(m.Item).where(m.Item.planned_start.isnot(None)).order_by(m.Item.planned_start)
    ).all()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Renovation Tracker//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for it in items:
        dtend = (it.planned_end or it.planned_start) + timedelta(days=1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:item-{it.id}@renovation-tracker",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{it.planned_start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape(it.title)}",
            f"DESCRIPTION:{_ics_escape(f'{it.status} / {it.category}')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(content=body, media_type="text/calendar")


# ---------- Budget ----------

def _bar_pcts(b: dict) -> dict | None:
    total = b["budget_max"]
    if not total:
        return None
    spent = min(b["spent"], total)
    committed = min(b["committed"], max(total - spent, 0))
    available = max(total - spent - committed, 0)
    return {"spent_pct": spent / total * 100, "committed_pct": committed / total * 100, "available_pct": available / total * 100}


@router.get("/budget")
def budget(request: Request, db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    phases = db.scalars(select(m.Phase).order_by(m.Phase.target_start.is_(None), m.Phase.target_start, m.Phase.id)).all()
    budgets = {p.id: planning.phase_budget(db, p) for p in phases}
    bars = {p.id: _bar_pcts(budgets[p.id]) for p in phases}
    entries = db.scalars(select(m.SavingsEntry).order_by(m.SavingsEntry.date.desc(), m.SavingsEntry.id.desc())).all()
    pot = planning.savings_pot(db)
    monthly = int(services.get_setting(db, "monthly_saving_target", "0"))
    threshold = int(services.get_setting(db, "quote_threshold", "100000"))
    projection = planning.savings_projection(db)
    return render(
        request, "budget.html", user, phases=phases, budgets=budgets, bars=bars, entries=entries, pot=pot,
        monthly=monthly, threshold=threshold, projection=projection,
    )


@router.post("/budget/phases")
def budget_add_phase(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), budget_min: str = Form(""), budget_max: str = Form(""),
    target_start: str = Form(""), notes: str = Form(""),
):
    services.create(
        db, actor(user), m.Phase, name=name, budget_min=to_pence(budget_min), budget_max=to_pence(budget_max),
        target_start=target_start or None, notes=notes,
    )
    db.commit()
    return RedirectResponse("/budget", status_code=303)


@router.post("/budget/phases/{phase_id}")
def budget_update_phase(
    phase_id: int, db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), budget_min: str = Form(""), budget_max: str = Form(""),
    target_start: str = Form(""), notes: str = Form(""),
):
    p = get_or_404(db, m.Phase, phase_id)
    services.update(db, actor(user), p, dict(
        name=name, budget_min=to_pence(budget_min), budget_max=to_pence(budget_max),
        target_start=target_start or None, notes=notes,
    ))
    db.commit()
    return RedirectResponse("/budget", status_code=303)


@router.post("/budget/savings")
def budget_add_saving(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    date_: str = Form(..., alias="date"), amount: str = Form(...), note: str = Form(""),
):
    services.create(db, actor(user), m.SavingsEntry, date=date_, amount=to_pence(amount), note=note)
    db.commit()
    return RedirectResponse("/budget", status_code=303)


@router.post("/budget/settings")
def budget_settings(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    monthly_saving_target: str = Form(""), quote_threshold: str = Form(""),
):
    services.set_setting(db, "monthly_saving_target", to_pence(monthly_saving_target) or 0)
    services.set_setting(db, "quote_threshold", to_pence(quote_threshold) or 0)
    db.commit()
    return RedirectResponse("/budget", status_code=303)


# ---------- Activity ----------

_ENTITY_URL = {"item": "/items/{id}", "contractor": "/contractors/{id}"}


def _entity_url(entity: str, entity_id: int) -> str | None:
    tpl = _ENTITY_URL.get(entity)
    return tpl.format(id=entity_id) if tpl else None


def _summarize(row: m.ActivityLog) -> str:
    if row.action == "create":
        return "created"
    if row.action == "delete":
        return "deleted"
    before = json.loads(row.before_json) if row.before_json else {}
    after = json.loads(row.after_json) if row.after_json else {}
    parts = [f"{k}: {before.get(k)} \u2192 {after.get(k)}" for k in after]
    return "; ".join(parts) if parts else "updated"


@router.get("/activity")
def activity(
    request: Request, db: Session = Depends(get_db), user: m.User = Depends(require_user),
    entity: str = "", via: str = "", user_id: str = "", page: int = 1, msg: str = "",
):
    query = select(m.ActivityLog)
    if entity:
        query = query.where(m.ActivityLog.entity == entity)
    if via:
        query = query.where(m.ActivityLog.via == via)
    if user_id:
        query = query.where(m.ActivityLog.user_id == int(user_id))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = max(page, 1)
    rows = db.scalars(query.order_by(m.ActivityLog.id.desc()).offset((page - 1) * 50).limit(50)).all()

    keys = {(r.entity, r.entity_id) for r in rows}
    latest_map = {}
    for ent, eid in keys:
        latest_map[(ent, eid)] = db.scalar(
            select(func.max(m.ActivityLog.id)).where(
                m.ActivityLog.entity == ent, m.ActivityLog.entity_id == eid, m.ActivityLog.undone.is_(False)
            )
        )

    entries = []
    for r in rows:
        can_undo = not r.undone and latest_map.get((r.entity, r.entity_id)) == r.id
        entries.append((r, _summarize(r), can_undo, _entity_url(r.entity, r.entity_id)))

    users = db.scalars(select(m.User).order_by(m.User.display_name)).all()
    return render(
        request, "activity.html", user, entries=entries, users=users, entity=entity, via=via,
        user_id=user_id, page=page, total=total, msg=msg, ENTITIES=sorted(services.ENTITIES.keys()),
    )


@router.post("/activity/{log_id}/undo")
def activity_undo(log_id: int, db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    try:
        services.undo(db, actor(user), log_id)
        db.commit()
    except ServiceError as e:
        db.rollback()
        return RedirectResponse(f"/activity?msg={urlquote(str(e))}", status_code=303)
    return RedirectResponse("/activity", status_code=303)


# ---------- Settings ----------

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), user: m.User = Depends(require_user), msg: str = ""):
    rooms = db.scalars(select(m.Room).order_by(m.Room.sort_order, m.Room.name)).all()
    clients = None
    try:
        from app import oauth
        raw = oauth.list_clients(db)
        clients = []
        for c in raw:
            if isinstance(c, dict):
                cid, cname = c.get("client_id"), c.get("client_name")
            else:
                cid, cname = getattr(c, "client_id", None), getattr(c, "client_name", None)
            clients.append({"client_id": cid, "client_name": cname or cid})
    except (ImportError, AttributeError):
        clients = None
    return render(request, "settings.html", user, rooms=rooms, clients=clients, msg=msg)


@router.post("/settings/rooms")
def settings_add_room(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), sort_order: str = Form("0"),
):
    services.create(db, actor(user), m.Room, name=name, sort_order=_int(sort_order) or 0)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/rooms/{room_id}")
def settings_update_room(
    room_id: int, db: Session = Depends(get_db), user: m.User = Depends(require_user),
    name: str = Form(...), sort_order: str = Form("0"),
):
    room = get_or_404(db, m.Room, room_id)
    services.update(db, actor(user), room, {"name": name, "sort_order": _int(sort_order) or 0})
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings/export.json")
def settings_export(db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    try:
        from app import backup
        data = backup.export_json(db)
    except (ImportError, AttributeError):
        data = {"error": "export not available yet"}
    body = json.dumps(data, default=str, indent=2)
    return Response(body, media_type="application/json", headers={"Content-Disposition": "attachment; filename=export.json"})


@router.post("/settings/clients/{client_id}/revoke")
def settings_revoke_client(client_id: str, db: Session = Depends(get_db), user: m.User = Depends(require_user)):
    try:
        from app import oauth
        oauth.revoke_client(db, client_id)
        db.commit()
    except (ImportError, AttributeError):
        pass
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
def settings_change_password(
    db: Session = Depends(get_db), user: m.User = Depends(require_user),
    current_password: str = Form(...), new_password: str = Form(...), new_password2: str = Form(...),
):
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    ph = PasswordHasher()
    try:
        ph.verify(user.password_hash, current_password)
    except VerifyMismatchError:
        return RedirectResponse(f"/settings?msg={urlquote('Current password is incorrect')}", status_code=303)
    if new_password != new_password2:
        return RedirectResponse(f"/settings?msg={urlquote('New passwords do not match')}", status_code=303)
    user.password_hash = auth.hash_password(new_password)
    db.commit()
    return RedirectResponse(f"/settings?msg={urlquote('Password updated')}", status_code=303)
