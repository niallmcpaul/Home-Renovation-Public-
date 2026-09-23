import functools
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app import db as dbm, models as m, services as s

NOISE = {"created_by", "updated_by"}

INSTRUCTIONS = f"""Renovation tracker for a Victorian end-terrace house (Bristol), shared by two householders.

Vocabularies (use exactly):
- item status: {", ".join(m.STATUSES)}. `proposed` means "suggested by Claude, awaiting human confirmation in the \
dashboard inbox". Items you create default to `proposed`; do not move them out of `proposed` unless the user asks.
- item category: {", ".join(m.CATEGORIES)} (urgent = safety, structural, survey defects; enabling = must precede \
other work).
- item size: {", ".join(m.SIZES)}.
- item source: {", ".join(m.SOURCES)}.
- quote status: {", ".join(m.QUOTE_STATUSES)}.
All money is integer pence (GBP 1,250.00 = 125000). Dates are ISO 8601 (YYYY-MM-DD).
Call list_rooms and list_phases to get valid room_id / phase_id values. There is no hard delete: use archive_item.
Dependencies: link_items(blocker_id, blocked_id) means the blocker must be done before the blocked item; cycles are \
rejected.

Survey workflow: when the user shares a building survey, extract defects and recommendations and call create_items \
once with all of them: source="survey", source_ref=the survey section (e.g. "D4.2"), category="urgent" where the \
surveyor flags safety or structural issues, otherwise enabling/improvement as appropriate; include the surveyor's \
wording in description and any Building Control / specialist requirements in compliance_notes. Items arrive as \
`proposed` for a human to confirm or discard in the dashboard inbox. Summarise what you created afterwards.
Before accept_quote, confirm with the user: it declines the item's other open quotes."""

VOCAB = (f"\nstatus: {'|'.join(m.STATUSES)} (proposed = suggested by Claude, awaiting human confirmation in the "
         f"dashboard inbox). category: {'|'.join(m.CATEGORIES)}. size: {'|'.join(m.SIZES)}. "
         f"source: {'|'.join(m.SOURCES)}. Money is integer pence; dates ISO 8601.")


def _actor() -> s.Actor:
    tok = get_access_token()
    return s.Actor(user_id=int(tok.subject) if tok and tok.subject else None, via="claude")


@contextmanager
def _session():
    db = dbm.SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            out = fn(*args, **kwargs)
        except s.ServiceError as e:
            out = {"error": str(e)}
        except IntegrityError as e:
            out = {"error": f"Invalid reference or duplicate: {e.orig}"}
        except ValueError as e:
            out = {"error": f"Invalid value: {e}"}
        return json.dumps(out, separators=(",", ":"), default=str)
    wrapper.__annotations__ = {**fn.__annotations__, "return": str}
    return wrapper


def _d(obj, compact: bool = False) -> dict:
    return {k: v for k, v in s.to_dict(obj).items() if k not in NOISE and not (compact and v in (None, False, ""))}


def _get(db, cls, id: int):
    obj = db.get(cls, id)
    if not obj:
        raise s.ServiceError(f"{s.ENTITY_NAMES[cls]} {id} not found")
    return obj


def _check_fields(cls, fields: dict, extra: set[str] = frozenset()):
    unknown = set(fields) - (set(s.columns(cls)) - s.READONLY) - extra
    if unknown:
        raise s.ServiceError(f"Unknown fields: {', '.join(sorted(unknown))}. Allowed: "
                             f"{', '.join(sorted(set(s.columns(cls)) - s.READONLY | extra))}")


def _blocked_ids(db) -> set[int]:
    try:
        from app.planning import blocked_ids
        return blocked_ids(db)
    except ImportError:
        done = {i for i, st in db.execute(select(m.Item.id, m.Item.status)) if st == "done"}
        return {b for b, blockers in s.blockers_map(db).items() if blockers - done}


def _summary(it: m.Item, blocked: set[int] | None = None) -> dict:
    out = {k: getattr(it, k) for k in ("id", "title", "status", "category", "size", "estimate_low", "estimate_high",
                                       "actual_cost", "source_ref", "decision_group") if getattr(it, k) is not None}
    out.update(room=it.room.name if it.room else None, phase=it.phase.name if it.phase else None)
    if blocked is not None:
        out["blocked"] = it.id in blocked
    return {k: v for k, v in out.items() if v is not None}


def _quote(q: m.Quote) -> dict:
    return {**_d(q, compact=True), "contractor": q.contractor.name if q.contractor else None}


def _activity(a: m.ActivityLog) -> dict:
    return {"id": a.id, "at": a.at.isoformat(), "user": a.user.display_name if a.user else None, "via": a.via,
            "entity": a.entity, "entity_id": a.entity_id, "action": a.action,
            "before": json.loads(a.before_json) if a.before_json else None,
            "after": json.loads(a.after_json) if a.after_json else None}


@_safe
def list_items(status: str | None = None, category: str | None = None, room_id: int | None = None,
               phase_id: int | None = None, size: str | None = None, text: str | None = None,
               blocked_only: bool = False, include_archived: bool = False, limit: int = 100) -> dict:
    """List items (compact). Filters: status, category, size (see server vocabularies), room_id, phase_id,
    text (matches title/description), blocked_only (items with an unfinished blocker). Archived items are
    excluded unless status='archived' or include_archived=true."""
    with _session() as db:
        q = select(m.Item)
        for col, val in ((m.Item.status, status), (m.Item.category, category), (m.Item.size, size),
                         (m.Item.room_id, room_id), (m.Item.phase_id, phase_id)):
            if val is not None:
                q = q.where(col == val)
        if status is None and not include_archived:
            q = q.where(m.Item.status != "archived")
        if text:
            q = q.where(or_(m.Item.title.ilike(f"%{text}%"), m.Item.description.ilike(f"%{text}%")))
        blocked = _blocked_ids(db)
        if blocked_only:
            q = q.where(m.Item.id.in_(blocked))
        items = db.scalars(q.order_by(m.Item.id).limit(max(1, min(limit, 500)))).unique().all()
        return {"count": len(items), "items": [_summary(it, blocked) for it in items]}


@_safe
def get_item(id: int) -> dict:
    """Full item: all fields, room/phase names, quotes (with contractor), blockers and items it blocks,
    decision-group alternatives, attachment metadata, notes and the last 10 history entries."""
    with _session() as db:
        it = _get(db, m.Item, id)
        blocked = _blocked_ids(db)
        deps = db.scalars(select(m.Dependency).where(
            or_(m.Dependency.blocker_item_id == id, m.Dependency.blocked_item_id == id))).all()
        mini = lambda i: _summary(db.get(m.Item, i), blocked)
        siblings = db.scalars(select(m.Item).where(
            m.Item.decision_group == it.decision_group, m.Item.id != id)).all() if it.decision_group else []
        history = db.scalars(select(m.ActivityLog).where(
            m.ActivityLog.entity == "item", m.ActivityLog.entity_id == id).order_by(m.ActivityLog.id.desc()).limit(10))
        return {
            **_d(it), "room": it.room.name if it.room else None, "phase": it.phase.name if it.phase else None,
            "blocked": id in blocked,
            "blocked_by": [mini(d.blocker_item_id) for d in deps if d.blocked_item_id == id],
            "blocks": [mini(d.blocked_item_id) for d in deps if d.blocker_item_id == id],
            "decision_group_alternatives": [_summary(x) for x in siblings],
            "quotes": [_quote(q) for q in it.quotes],
            "attachments": [{k: getattr(a, k) for k in ("id", "kind", "filename", "mime_type", "size_bytes")}
                            for a in it.attachments],
            "notes": [{"id": n.id, "body": n.body, "at": n.created_at.isoformat(), "by": n.created_by}
                      for n in it.notes],
            "history": [_activity(a) for a in history],
        }


@_safe
def create_items(items: list[dict[str, Any]]) -> dict:
    """Batch-create items. Each dict needs `title`; optional: description, size, category, status, room_id,
    phase_id, estimate_low, estimate_high (pence), planned_start, planned_end, needs_building_control,
    needs_planning_check, compliance_notes, source, source_ref, decision_group, and blocked_by (list of existing
    item ids that must be done first). Defaults: status='proposed', source='claude', size='task',
    category='improvement'. For survey imports set source='survey' and source_ref to the survey section.
    All-or-nothing: if any item is invalid nothing is created."""
    actor = _actor()
    with _session() as db:
        created = []
        for i, raw in enumerate(items):
            fields = dict(raw)
            blocked_by = fields.pop("blocked_by", None) or []
            try:
                _check_fields(m.Item, fields)
                if not fields.get("title"):
                    raise s.ServiceError("title is required")
                fields = {"status": "proposed", "source": "claude", "size": "task", "category": "improvement",
                          **fields}
                it = s.create(db, actor, m.Item, **fields)
                for b in blocked_by:
                    s.link_items(db, actor, int(b), it.id)
            except s.ServiceError as e:
                raise s.ServiceError(f"items[{i}] ({raw.get('title')!r}): {e}") from e
            created.append({"id": it.id, "title": it.title, "status": it.status, "category": it.category})
        return {"created": created}


@_safe
def update_item(id: int, fields: dict[str, Any], expected_updated_at: str | None = None) -> dict:
    """Update any editable item field, including status (see vocabularies). Money in pence, dates ISO.
    Pass expected_updated_at (from get_item) to guard against overwriting a concurrent human edit."""
    with _session() as db:
        _check_fields(m.Item, fields)
        it = s.update(db, _actor(), _get(db, m.Item, id), fields, expected_updated_at)
        return _d(it)


@_safe
def add_note(item_id: int, body: str) -> dict:
    """Append a note to an item's discussion thread."""
    with _session() as db:
        _get(db, m.Item, item_id)
        return _d(s.create(db, _actor(), m.Note, item_id=item_id, body=body))


@_safe
def link_items(blocker_id: int, blocked_id: int) -> dict:
    """Record that blocker_id must be done before blocked_id can start. Rejects cycles."""
    with _session() as db:
        d = s.link_items(db, _actor(), blocker_id, blocked_id)
        return {"id": d.id, "blocker_id": blocker_id, "blocked_id": blocked_id}


@_safe
def unlink_items(blocker_id: int, blocked_id: int) -> dict:
    """Remove a dependency between two items."""
    with _session() as db:
        return {"removed": s.unlink_items(db, _actor(), blocker_id, blocked_id)}


@_safe
def add_quote(item_id: int, amount_pence: int | None = None, contractor_id: int | None = None,
              includes_vat: bool = True, scope: str | None = None, exclusions: str | None = None,
              received_on: str | None = None, valid_until: str | None = None, status: str | None = None) -> dict:
    """Add a quote to an item. amount_pence is integer pence. status (requested|received|accepted|declined|expired)
    defaults to 'received' when an amount is given, else 'requested'."""
    with _session() as db:
        _get(db, m.Item, item_id)
        q = s.create(db, _actor(), m.Quote, item_id=item_id, amount=amount_pence, contractor_id=contractor_id,
                     includes_vat=includes_vat, scope=scope, exclusions=exclusions, received_on=received_on,
                     valid_until=valid_until, status=status or ("received" if amount_pence is not None else "requested"))
        return _quote(q)


@_safe
def update_quote(id: int, fields: dict[str, Any]) -> dict:
    """Update quote fields: amount (or amount_pence), contractor_id, includes_vat, scope, exclusions, received_on,
    valid_until, status. Use accept_quote to accept."""
    with _session() as db:
        fields = dict(fields)
        if "amount_pence" in fields:
            fields["amount"] = fields.pop("amount_pence")
        _check_fields(m.Quote, fields)
        return _quote(s.update(db, _actor(), _get(db, m.Quote, id), fields))


@_safe
def accept_quote(id: int) -> dict:
    """Accept a quote and decline the item's other open quotes. Confirm with the user before calling."""
    with _session() as db:
        q = _get(db, m.Quote, id)
        declined = s.accept_quote(db, _actor(), q)
        return {"accepted": _quote(q), "declined_ids": [d.id for d in declined]}


@_safe
def choose_option(item_id: int) -> dict:
    """Choose one item as the pick for its decision_group: parks every other non-archived item in that group and,
    if this item is proposed/idea, advances it to researching. Use only when the user has explicitly chosen between
    mutually exclusive options."""
    with _session() as db:
        item = _get(db, m.Item, item_id)
        parked = s.choose_option(db, _actor(), item)
        return {"chosen": _summary(item), "parked_ids": [p.id for p in parked]}


@_safe
def list_contractors(text: str | None = None) -> dict:
    """List contractors; optional text filter on name, company or trades."""
    with _session() as db:
        q = select(m.Contractor).order_by(m.Contractor.name)
        if text:
            q = q.where(or_(*(c.ilike(f"%{text}%") for c in (m.Contractor.name, m.Contractor.company,
                                                              m.Contractor.trades))))
        return {"contractors": [_d(c, compact=True) for c in db.scalars(q)]}


@_safe
def upsert_contractor(fields: dict[str, Any], id: int | None = None) -> dict:
    """Create a contractor (omit id; `name` required) or update one by id. Fields: name, company, trades, phone,
    email, website, recommended_by, registrations, insurance_checked, rating (1-5), notes."""
    with _session() as db:
        _check_fields(m.Contractor, fields)
        if id is None:
            if not fields.get("name"):
                raise s.ServiceError("name is required")
            return _d(s.create(db, _actor(), m.Contractor, **fields))
        return _d(s.update(db, _actor(), _get(db, m.Contractor, id), fields))


@_safe
def list_rooms() -> dict:
    """Rooms with ids, for room_id."""
    with _session() as db:
        return {"rooms": [{"id": r.id, "name": r.name} for r in db.scalars(select(m.Room).order_by(m.Room.sort_order))]}


@_safe
def list_phases() -> dict:
    """Budget phases with ids (for phase_id) and budgets in pence."""
    with _session() as db:
        return {"phases": [_d(p, compact=True) for p in db.scalars(select(m.Phase).order_by(m.Phase.id))]}


@_safe
def budget_summary(phase_id: int | None = None) -> dict:
    """Per-phase budget (pence): committed, spent, available; savings pot, monthly target and the projected month
    each unfunded 'Later' item becomes affordable."""
    from app import planning
    with _session() as db:
        phases = [_get(db, m.Phase, phase_id)] if phase_id else db.scalars(select(m.Phase).order_by(m.Phase.id)).all()
        proj = planning.savings_projection(db)
        return {
            "phases": [{"id": p.id, "name": p.name, **planning.phase_budget(db, p)} for p in phases],
            "savings_pot": proj["pot"], "monthly_saving_target": proj["monthly"],
            "projection": [{"item_id": q["item"].id, "title": q["item"].title, "cost": q["cost"],
                            "affordable_month": q["affordable_month"].isoformat() if q["affordable_month"] else None}
                           for q in proj["queue"]],
        }


@_safe
def next_actions(limit: int = 10) -> dict:
    """Items ready to start (not blocked, not done/parked/proposed), ordered urgent > enabling > improvement > wish,
    then by how many items they unblock, then cheapest. Flags over_budget and needs_quotes (received count)."""
    from app import planning
    with _session() as db:
        return {"actions": [{**_summary(a["item"]), "blocks": a["blocks"], "over_budget": a["over_budget"],
                             "needs_quotes": a["needs_quotes"]} for a in planning.next_actions(db, limit)]}


@_safe
def recent_activity(since: str | None = None, limit: int = 50) -> dict:
    """Change log, newest first. since: ISO datetime/date (UTC)."""
    with _session() as db:
        q = select(m.ActivityLog).order_by(m.ActivityLog.id.desc()).limit(max(1, min(limit, 200)))
        if since:
            dt = datetime.fromisoformat(since)
            q = q.where(m.ActivityLog.at >= (dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt))
        return {"activity": [_activity(a) for a in db.scalars(q)]}


@_safe
def archive_item(id: int) -> dict:
    """Archive an item (sets status 'archived'). There is no hard delete."""
    with _session() as db:
        return _summary(s.archive_item(db, _actor(), _get(db, m.Item, id)))


TOOLS = [list_items, get_item, create_items, update_item, add_note, link_items, unlink_items, add_quote, update_quote,
         accept_quote, choose_option, list_contractors, upsert_contractor, list_rooms, list_phases, budget_summary,
         next_actions, recent_activity, archive_item]


READ_ONLY = {list_items, get_item, list_contractors, list_rooms, list_phases, budget_summary, next_actions,
             recent_activity}


def build_server(**kwargs) -> MCPServer:
    server = MCPServer(name="renovation-tracker", title="Renovation Tracker", instructions=INSTRUCTIONS, **kwargs)
    for fn in TOOLS:
        doc = " ".join((fn.__doc__ or "").split())
        server.add_tool(fn, description=doc + VOCAB if fn in (list_items, create_items, update_item) else doc,
                        annotations=ToolAnnotations(read_only_hint=fn in READ_ONLY, destructive_hint=False))
    return server
