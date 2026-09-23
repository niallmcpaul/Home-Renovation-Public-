import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app import models as m

ENTITIES = {
    "item": m.Item, "room": m.Room, "phase": m.Phase, "dependency": m.Dependency, "quote": m.Quote,
    "contractor": m.Contractor, "attachment": m.Attachment, "note": m.Note, "savings_entry": m.SavingsEntry,
}
ENTITY_NAMES = {v: k for k, v in ENTITIES.items()}
READONLY = {"id", "created_at", "updated_at", "created_by", "updated_by"}


class ServiceError(ValueError):
    pass


class ConflictError(ServiceError):
    def __init__(self, current: dict):
        super().__init__("Record was changed by someone else")
        self.current = current


@dataclass
class Actor:
    user_id: int | None
    via: str = "web"
    batch_id: str | None = None


@contextmanager
def batch(actor: Actor):
    prev, actor.batch_id = actor.batch_id, actor.batch_id or uuid.uuid4().hex
    try:
        yield
    finally:
        actor.batch_id = prev


def columns(cls) -> list[str]:
    return [c.key for c in inspect(cls).mapper.column_attrs]


def to_dict(obj) -> dict:
    out = {}
    for k in columns(type(obj)):
        v = getattr(obj, k)
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _coerce(cls, fields: dict) -> dict:
    cols = inspect(cls).mapper.columns
    out = {}
    for k, v in fields.items():
        if k in READONLY or k not in cols:
            continue
        py = cols[k].type.python_type
        if isinstance(v, str) and py in (date, datetime):
            v = py.fromisoformat(v) if v else None
        elif v == "" and py is not str:
            v = None
        out[k] = v
    return out


def _validate(cls, fields: dict):
    checks = {"size": m.SIZES, "category": m.CATEGORIES, "source": m.SOURCES}
    checks["status"] = m.QUOTE_STATUSES if cls is m.Quote else m.STATUSES
    if cls is m.Attachment:
        checks = {"kind": m.ATTACHMENT_KINDS}
    for k, allowed in checks.items():
        if k in fields and hasattr(cls, k) and fields[k] not in allowed:
            raise ServiceError(f"Invalid {k} '{fields[k]}'. Allowed: {', '.join(allowed)}")
    if cls is m.Contractor and fields.get("rating") is not None and not 1 <= int(fields["rating"]) <= 5:
        raise ServiceError("rating must be 1-5")


def log(db: Session, actor: Actor, entity: str, entity_id: int, action: str, before: dict | None, after: dict | None):
    db.add(m.ActivityLog(
        user_id=actor.user_id, via=actor.via, batch_id=actor.batch_id, entity=entity, entity_id=entity_id, action=action,
        before_json=json.dumps(before, default=str) if before is not None else None,
        after_json=json.dumps(after, default=str) if after is not None else None,
    ))


def create(db: Session, actor: Actor, cls, **fields):
    fields = _coerce(cls, fields)
    _validate(cls, fields)
    obj = cls(**fields)
    if hasattr(obj, "created_by"):
        obj.created_by = obj.updated_by = actor.user_id
    db.add(obj)
    db.flush()
    log(db, actor, ENTITY_NAMES[cls], obj.id, "create", None, to_dict(obj))
    return obj


def update(db: Session, actor: Actor, obj, fields: dict, expected_updated_at: str | datetime | None = None):
    if expected_updated_at:
        if isinstance(expected_updated_at, str):
            expected_updated_at = datetime.fromisoformat(expected_updated_at)
        if obj.updated_at.replace(microsecond=0) != expected_updated_at.replace(microsecond=0):
            raise ConflictError(to_dict(obj))
    fields = _coerce(type(obj), fields)
    _validate(type(obj), fields)
    before = to_dict(obj)
    for k, v in fields.items():
        setattr(obj, k, v)
    obj.updated_by = actor.user_id
    if isinstance(obj, m.Item) and fields.get("status") == "done" and not obj.completed_on:
        obj.completed_on = date.today()
    db.flush()
    after = to_dict(obj)
    changed_before = {k: before[k] for k in after if k not in READONLY and before[k] != after[k]}
    if changed_before:
        log(db, actor, ENTITY_NAMES[type(obj)], obj.id, "update", changed_before, {k: after[k] for k in changed_before})
    return obj


def delete(db: Session, actor: Actor, obj):
    log(db, actor, ENTITY_NAMES[type(obj)], obj.id, "delete", to_dict(obj), None)
    db.delete(obj)
    db.flush()


def archive_item(db: Session, actor: Actor, item: m.Item):
    return update(db, actor, item, {"status": "archived"})


def blockers_map(db: Session) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = {}
    for d in db.scalars(select(m.Dependency)):
        graph.setdefault(d.blocked_item_id, set()).add(d.blocker_item_id)
    return graph


def would_cycle(graph: dict[int, set[int]], blocker_id: int, blocked_id: int) -> bool:
    """graph maps blocked -> blockers. Adding blocker->blocked cycles if blocked already (transitively) blocks blocker."""
    if blocker_id == blocked_id:
        return True
    stack, seen = [blocker_id], set()
    while stack:
        n = stack.pop()
        if n == blocked_id:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, ()))
    return False


def link_items(db: Session, actor: Actor, blocker_id: int, blocked_id: int) -> m.Dependency:
    for i in (blocker_id, blocked_id):
        if not db.get(m.Item, i):
            raise ServiceError(f"Item {i} not found")
    existing = db.scalar(select(m.Dependency).filter_by(blocker_item_id=blocker_id, blocked_item_id=blocked_id))
    if existing:
        return existing
    if would_cycle(blockers_map(db), blocker_id, blocked_id):
        raise ServiceError(f"Linking {blocker_id} -> {blocked_id} would create a dependency cycle")
    return create(db, actor, m.Dependency, blocker_item_id=blocker_id, blocked_item_id=blocked_id)


def unlink_items(db: Session, actor: Actor, blocker_id: int, blocked_id: int) -> bool:
    dep = db.scalar(select(m.Dependency).filter_by(blocker_item_id=blocker_id, blocked_item_id=blocked_id))
    if dep:
        delete(db, actor, dep)
    return dep is not None


def accept_quote(db: Session, actor: Actor, quote: m.Quote) -> list[m.Quote]:
    """Accepts the quote and declines other open quotes for the same item. Callers confirm with the user first."""
    declined = []
    with batch(actor):
        for q in db.scalars(select(m.Quote).where(m.Quote.item_id == quote.item_id, m.Quote.id != quote.id)):
            if q.status in ("requested", "received"):
                update(db, actor, q, {"status": "declined"})
                declined.append(q)
        update(db, actor, quote, {"status": "accepted"})
        item = quote.item
        if m.STATUSES.index(item.status) < m.STATUSES.index("approved"):
            update(db, actor, item, {"status": "approved"})
    return declined


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    s = db.get(m.Setting, key)
    return s.value if s else default


def set_setting(db: Session, key: str, value) -> None:
    s = db.get(m.Setting, key)
    if s:
        s.value = str(value)
    else:
        db.add(m.Setting(key=key, value=str(value)))
    db.flush()


def undo_rows(db: Session, entry: m.ActivityLog) -> list[m.ActivityLog]:
    if not entry.batch_id:
        return [entry]
    return list(db.scalars(select(m.ActivityLog).where(
        m.ActivityLog.batch_id == entry.batch_id, m.ActivityLog.undone.is_(False)
    ).order_by(m.ActivityLog.id.desc())))


def undo_blocker(db: Session, rows: list[m.ActivityLog]) -> m.ActivityLog | None:
    ids = {r.id for r in rows}
    for r in rows:
        latest = db.scalar(select(func.max(m.ActivityLog.id)).where(
            m.ActivityLog.entity == r.entity, m.ActivityLog.entity_id == r.entity_id, m.ActivityLog.undone.is_(False)
        ))
        if latest not in ids:
            return r
    return None


def _undo_one(db: Session, actor: Actor, entry: m.ActivityLog):
    cls = ENTITIES[entry.entity]
    obj = db.get(cls, entry.entity_id)
    before = json.loads(entry.before_json) if entry.before_json else None
    if entry.action == "create" and obj:
        delete(db, actor, obj)
    elif entry.action == "update" and obj:
        update(db, actor, obj, before)
    elif entry.action == "delete":
        restored = cls(**_coerce(cls, before))
        restored.id = before["id"]
        db.add(restored)
        db.flush()
        log(db, actor, entry.entity, restored.id, "create", None, to_dict(restored))
    else:
        raise ServiceError("Record no longer exists")
    entry.undone = True
    db.query(m.ActivityLog).filter(m.ActivityLog.id > entry.id, m.ActivityLog.entity == entry.entity,
                                   m.ActivityLog.entity_id == entry.entity_id).update({"undone": True})
    db.flush()


def undo(db: Session, actor: Actor, log_id: int):
    entry = db.get(m.ActivityLog, log_id)
    if not entry or entry.undone:
        raise ServiceError("Nothing to undo")
    rows = undo_rows(db, entry)
    blocker = undo_blocker(db, rows)
    if blocker and not entry.batch_id:
        raise ServiceError("Only the most recent change to a record can be undone")
    if blocker:
        raise ServiceError(f"{blocker.entity} #{blocker.entity_id} has changed since; undo that change first")
    for r in rows:
        _undo_one(db, actor, r)


def choose_option(db: Session, actor: Actor, item: m.Item) -> list[m.Item]:
    """Parks every other non-archived item in item's decision_group; advances item out of proposed/idea."""
    if not item.decision_group:
        raise ServiceError("Item has no decision_group")
    siblings = list(db.scalars(select(m.Item).where(
        m.Item.decision_group == item.decision_group, m.Item.id != item.id, m.Item.status.notin_(("archived", "done", "parked"))
    )))
    with batch(actor):
        for sib in siblings:
            update(db, actor, sib, {"status": "parked"})
        if item.status in ("proposed", "idea"):
            update(db, actor, item, {"status": "researching"})
    return siblings
