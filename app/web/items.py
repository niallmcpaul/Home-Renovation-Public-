import io
import json
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, ImageOps
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config, models as m, planning, services
from app.db import get_db
from app.web.common import actor, get_or_404, render, require_user, templates, to_pence

router = APIRouter(dependencies=[Depends(require_user)])

templates.env.filters["from_json"] = json.loads

FIELD_META = [
    ("title", "Title", "text"), ("description", "Description", "text"), ("size", "Size", "text"),
    ("category", "Category", "text"), ("status", "Status", "text"), ("room_id", "Room", "text"),
    ("phase_id", "Phase", "text"), ("estimate_low", "Estimate low", "money"), ("estimate_high", "Estimate high", "money"),
    ("actual_cost", "Actual cost", "money"), ("planned_start", "Planned start", "text"), ("planned_end", "Planned end", "text"),
    ("completed_on", "Completed on", "text"), ("needs_building_control", "Needs building control", "bool"),
    ("needs_planning_check", "Needs planning check", "bool"), ("compliance_notes", "Compliance notes", "text"),
    ("source", "Source", "text"), ("source_ref", "Source ref", "text"), ("decision_group", "Decision group", "text"),
]

SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", "jpg", True),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png", True),
    (b"GIF87a", "image/gif", "gif", True),
    (b"GIF89a", "image/gif", "gif", True),
    (b"%PDF-", "application/pdf", "pdf", False),
]
HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis", b"hevm", b"hevs")


def sniff_type(data: bytes):
    for magic, mime, ext, is_image in SIGNATURES:
        if data.startswith(magic):
            return mime, ext, is_image
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp", True
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS:
        return "image/heic", "heic", True
    return None


def referer_path(request: Request, fallback: str) -> str:
    ref = request.headers.get("referer")
    if not ref:
        return fallback
    parts = urlsplit(ref)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def with_msg(url: str, msg: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["msg"] = msg
    return f"{parts.path}?{urlencode(query)}"


def pounds(pence: int | None) -> str:
    return f"{pence / 100:.2f}" if pence is not None else ""


def form_values(item: m.Item, submitted: dict | None = None) -> dict:
    vals = {
        "title": item.title, "description": item.description or "", "size": item.size,
        "category": item.category, "status": item.status,
        "room_id": str(item.room_id) if item.room_id else "", "phase_id": str(item.phase_id) if item.phase_id else "",
        "estimate_low": pounds(item.estimate_low), "estimate_high": pounds(item.estimate_high),
        "actual_cost": pounds(item.actual_cost),
        "planned_start": item.planned_start.isoformat() if item.planned_start else "",
        "planned_end": item.planned_end.isoformat() if item.planned_end else "",
        "completed_on": item.completed_on.isoformat() if item.completed_on else "",
        "needs_building_control": item.needs_building_control, "needs_planning_check": item.needs_planning_check,
        "compliance_notes": item.compliance_notes or "", "source": item.source, "source_ref": item.source_ref or "",
        "decision_group": item.decision_group or "",
    }
    if submitted:
        for k in vals:
            if k not in submitted:
                continue
            v = submitted[k]
            if k in ("estimate_low", "estimate_high", "actual_cost"):
                vals[k] = pounds(v)
            elif k in ("room_id", "phase_id"):
                vals[k] = str(v) if v else ""
            elif k in ("needs_building_control", "needs_planning_check"):
                vals[k] = bool(v)
            else:
                vals[k] = v or ""
    return vals


def item_detail_ctx(db: Session, item: m.Item, **extra) -> dict:
    rooms = list(db.scalars(select(m.Room).order_by(m.Room.sort_order)))
    phases = list(db.scalars(select(m.Phase)))
    contractors = list(db.scalars(select(m.Contractor).order_by(m.Contractor.name)))
    siblings = []
    if item.decision_group:
        siblings = list(db.scalars(
            select(m.Item).where(m.Item.decision_group == item.decision_group, m.Item.id != item.id)
        ))
    history = list(db.scalars(
        select(m.ActivityLog).where(m.ActivityLog.entity == "item", m.ActivityLog.entity_id == item.id)
        .order_by(m.ActivityLog.id.desc()).limit(20)
    ))
    expiring_ids = {q.id for q in planning.expiring_quotes(db, 14) if q.item_id == item.id}
    ctx = dict(item=item, rooms=rooms, phases=phases, contractors=contractors,
               blockers=planning.blockers_of(db, item.id), blocked_by=planning.blocked_by_me(db, item.id),
               siblings=siblings, history=history, quote_expiring_ids=expiring_ids, field_meta=FIELD_META,
               conflict_current=None, conflict_submitted=None, error=None)
    ctx.update(extra)
    ctx.setdefault("vals", form_values(item))
    return ctx


def bar_segments(budget: dict) -> dict | None:
    total = budget.get("budget_max")
    if not total:
        return None
    spent_pct = min(100, budget["spent"] / total * 100)
    committed_pct = min(100 - spent_pct, max(0, budget["committed"] / total * 100))
    available_pct = max(0, 100 - spent_pct - committed_pct)
    return {"spent_pct": spent_pct, "committed_pct": committed_pct, "available_pct": available_pct}


@router.post("/quick-add")
def quick_add(request: Request, title: str = Form(...), user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    title = title.strip()
    if not title:
        return RedirectResponse(referer_path(request, "/"), status_code=303)
    item = services.create(db, actor(user), m.Item, title=title, size="wish", category="wish", status="idea")
    db.commit()
    dest = referer_path(request, f"/items/{item.id}")
    return RedirectResponse(with_msg(dest, f'Added "{title}"'), status_code=303)


@router.get("/")
def dashboard(request: Request, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    phase1 = db.scalar(select(m.Phase).where(m.Phase.name == "Phase 1"))
    budget = planning.phase_budget(db, phase1) if phase1 else None
    bar = bar_segments(budget) if budget else None
    actions = planning.next_actions(db, limit=10)
    blocked_ids = planning.blocked_ids(db)
    blocked_items = []
    if blocked_ids:
        blocked_items = list(db.scalars(
            select(m.Item).where(m.Item.id.in_(blocked_ids), m.Item.status.notin_(("done", "archived", "parked")))
        ))
    proposed = list(db.scalars(select(m.Item).where(m.Item.status == "proposed").order_by(m.Item.created_at)))
    expiring = planning.expiring_quotes(db, 14)
    return render(request, "dashboard.html", user, phase1=phase1, budget=budget, bar=bar, actions=actions,
                  blocked_items=blocked_items, proposed=proposed, expiring=expiring)


@router.get("/items")
def items_list(request: Request, status: str = "", category: str = "", room_id: str = "", phase_id: str = "",
               size: str = "", q: str = "", sort: str = "next", user: m.User = Depends(require_user),
               db: Session = Depends(get_db)):
    query = select(m.Item)
    if status:
        query = query.where(m.Item.status == status)
    else:
        query = query.where(m.Item.status != "archived")
    if category:
        query = query.where(m.Item.category == category)
    if room_id:
        query = query.where(m.Item.room_id == int(room_id))
    if phase_id:
        query = query.where(m.Item.phase_id == int(phase_id))
    if size:
        query = query.where(m.Item.size == size)
    if q:
        like = f"%{q}%"
        query = query.where(or_(m.Item.title.ilike(like), m.Item.description.ilike(like)))
    items = list(db.scalars(query))

    counts = planning.blocks_count(db)
    blocked_set = planning.blocked_ids(db)
    nq = planning.needs_quotes(db)

    if sort == "updated":
        items.sort(key=lambda it: it.updated_at, reverse=True)
    elif sort == "estimate":
        items.sort(key=lambda it: it.estimate_high if it.estimate_high is not None else float("inf"))
    elif sort == "title":
        items.sort(key=lambda it: it.title.lower())
    else:
        rank = {c: i for i, c in enumerate(m.CATEGORIES)}
        items.sort(key=lambda it: (rank.get(it.category, len(rank)), -counts.get(it.id, 0),
                                    it.estimate_high if it.estimate_high is not None else float("inf")))

    rooms = list(db.scalars(select(m.Room).order_by(m.Room.sort_order)))
    phases = list(db.scalars(select(m.Phase)))
    f = {"status": status, "category": category, "room_id": room_id, "phase_id": phase_id, "size": size, "q": q, "sort": sort}
    return render(request, "items.html", user, items=items, blocked_set=blocked_set, counts=counts, nq=nq,
                  rooms=rooms, phases=phases, f=f)


@router.get("/items/{item_id}")
def item_detail(request: Request, item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    return render(request, "item_detail.html", user, **item_detail_ctx(db, item))


@router.post("/items/{item_id}")
async def item_update(request: Request, item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    form = await request.form()
    fields = {
        "title": (form.get("title") or "").strip(),
        "description": form.get("description") or None,
        "size": form.get("size"),
        "category": form.get("category"),
        "status": form.get("status"),
        "room_id": int(form["room_id"]) if form.get("room_id") else None,
        "phase_id": int(form["phase_id"]) if form.get("phase_id") else None,
        "estimate_low": to_pence(form.get("estimate_low")),
        "estimate_high": to_pence(form.get("estimate_high")),
        "actual_cost": to_pence(form.get("actual_cost")),
        "planned_start": form.get("planned_start") or None,
        "planned_end": form.get("planned_end") or None,
        "completed_on": form.get("completed_on") or None,
        "needs_building_control": "needs_building_control" in form,
        "needs_planning_check": "needs_planning_check" in form,
        "compliance_notes": form.get("compliance_notes") or None,
        "source": form.get("source"),
        "source_ref": form.get("source_ref") or None,
        "decision_group": form.get("decision_group") or None,
    }
    try:
        services.update(db, actor(user), item, fields, expected_updated_at=form.get("updated_at"))
        db.commit()
    except services.ConflictError as e:
        db.rollback()
        ctx = item_detail_ctx(db, item, conflict_current=e.current, conflict_submitted=fields,
                               vals=form_values(item, fields))
        return render(request, "item_detail.html", user, status_code=409, **ctx)
    except services.ServiceError as e:
        db.rollback()
        ctx = item_detail_ctx(db, item, error=str(e), vals=form_values(item, fields))
        return render(request, "item_detail.html", user, status_code=400, **ctx)
    return RedirectResponse(with_msg(f"/items/{item.id}", "Saved"), status_code=303)


@router.post("/items/{item_id}/archive")
def archive(item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    services.archive_item(db, actor(user), item)
    db.commit()
    return RedirectResponse(with_msg("/items", "Archived"), status_code=303)


@router.post("/items/{item_id}/confirm")
def confirm_proposed(item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    services.update(db, actor(user), item, {"status": "idea"})
    db.commit()
    return RedirectResponse(with_msg("/", "Confirmed"), status_code=303)


@router.post("/items/{item_id}/discard")
def discard_proposed(item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    services.archive_item(db, actor(user), item)
    db.commit()
    return RedirectResponse(with_msg("/", "Discarded"), status_code=303)


@router.get("/items/{item_id}/link-search")
def link_search(request: Request, item_id: int, q: str = "", user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, m.Item, item_id)
    results = []
    if q.strip():
        like = f"%{q.strip()}%"
        results = list(db.scalars(
            select(m.Item).where(m.Item.id != item_id, m.Item.status != "archived", m.Item.title.ilike(like)).limit(10)
        ))
    return render(request, "_link_search_results.html", user, item=item, results=results)


@router.post("/items/{item_id}/dependencies")
def add_dependency(item_id: int, other_id: int = Form(...), role: str = Form(...),
                    user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    get_or_404(db, m.Item, item_id)
    blocker_id, blocked_id = (other_id, item_id) if role == "blocker" else (item_id, other_id)
    try:
        services.link_items(db, actor(user), blocker_id, blocked_id)
        db.commit()
        msg = "Linked"
    except services.ServiceError as e:
        db.rollback()
        msg = str(e)
    return RedirectResponse(with_msg(f"/items/{item_id}", msg), status_code=303)


@router.post("/items/{item_id}/dependencies/unlink")
def remove_dependency(item_id: int, other_id: int = Form(...), role: str = Form(...),
                       user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    blocker_id, blocked_id = (other_id, item_id) if role == "blocker" else (item_id, other_id)
    services.unlink_items(db, actor(user), blocker_id, blocked_id)
    db.commit()
    return RedirectResponse(with_msg(f"/items/{item_id}", "Unlinked"), status_code=303)


@router.post("/items/{item_id}/quotes")
async def add_quote(request: Request, item_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    get_or_404(db, m.Item, item_id)
    form = await request.form()
    fields = dict(
        item_id=item_id,
        contractor_id=int(form["contractor_id"]) if form.get("contractor_id") else None,
        amount=to_pence(form.get("amount")),
        includes_vat="includes_vat" in form,
        scope=form.get("scope") or None,
        exclusions=form.get("exclusions") or None,
        received_on=form.get("received_on") or None,
        valid_until=form.get("valid_until") or None,
        status=form.get("status") or "requested",
    )
    try:
        services.create(db, actor(user), m.Quote, **fields)
        db.commit()
        msg = "Quote added"
    except services.ServiceError as e:
        db.rollback()
        msg = str(e)
    return RedirectResponse(with_msg(f"/items/{item_id}", msg), status_code=303)


@router.post("/items/{item_id}/quotes/{quote_id}")
async def update_quote(request: Request, item_id: int, quote_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    quote = get_or_404(db, m.Quote, quote_id)
    form = await request.form()
    fields = {k: v for k, v in {"status": form.get("status")}.items() if v}
    try:
        services.update(db, actor(user), quote, fields)
        db.commit()
        msg = "Quote updated"
    except services.ServiceError as e:
        db.rollback()
        msg = str(e)
    return RedirectResponse(with_msg(f"/items/{item_id}", msg), status_code=303)


@router.post("/items/{item_id}/quotes/{quote_id}/accept")
def accept_quote(item_id: int, quote_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    quote = get_or_404(db, m.Quote, quote_id)
    services.accept_quote(db, actor(user), quote)
    db.commit()
    return RedirectResponse(with_msg(f"/items/{item_id}", "Quote accepted"), status_code=303)


@router.post("/items/{item_id}/attachments")
async def add_attachment(item_id: int, kind: str = Form("photo"), file: UploadFile = File(...),
                          user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    get_or_404(db, m.Item, item_id)
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        return RedirectResponse(with_msg(f"/items/{item_id}", "File too large (max 20MB)"), status_code=303)
    sniffed = sniff_type(data)
    if not sniffed:
        return RedirectResponse(with_msg(f"/items/{item_id}", "Unsupported file type"), status_code=303)
    mime, ext, is_image = sniffed
    if is_image:
        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((2000, 2000))
            buf = io.BytesIO()
            fmt = "JPEG" if ext == "jpg" else ext.upper()
            img.save(buf, format=fmt)
            data = buf.getvalue()
        except Exception:
            pass
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}.{ext}"
    (config.UPLOAD_DIR / stored_name).write_bytes(data)
    services.create(db, actor(user), m.Attachment, item_id=item_id, kind=kind, filename=file.filename or stored_name,
                     stored_path=stored_name, mime_type=mime, size_bytes=len(data))
    db.commit()
    return RedirectResponse(with_msg(f"/items/{item_id}", "Uploaded"), status_code=303)


@router.get("/uploads/{attachment_id}")
def serve_upload(attachment_id: int, user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    att = get_or_404(db, m.Attachment, attachment_id)
    path = config.UPLOAD_DIR / att.stored_path
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type=att.mime_type, filename=att.filename)


@router.post("/items/{item_id}/notes")
def add_note(item_id: int, body: str = Form(...), user: m.User = Depends(require_user), db: Session = Depends(get_db)):
    get_or_404(db, m.Item, item_id)
    body = body.strip()
    if body:
        services.create(db, actor(user), m.Note, item_id=item_id, body=body)
        db.commit()
    return RedirectResponse(with_msg(f"/items/{item_id}", "Note added"), status_code=303)
