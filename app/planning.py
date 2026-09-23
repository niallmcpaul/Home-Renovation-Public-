from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import models as m
from app import services

NOT_ACTIVE = ("done", "archived", "parked")


def blocked_ids(db: Session) -> set[int]:
    rows = db.scalars(
        select(m.Dependency.blocked_item_id)
        .join(m.Item, m.Item.id == m.Dependency.blocker_item_id)
        .where(m.Item.status.notin_(("done", "archived")))
    )
    return set(rows)


def blocks_count(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(m.Dependency.blocker_item_id, m.Item.status)
        .join(m.Item, m.Item.id == m.Dependency.blocked_item_id)
    ).all()
    counts: dict[int, int] = {}
    for blocker_id, status in rows:
        if status not in ("done", "archived"):
            counts[blocker_id] = counts.get(blocker_id, 0) + 1
    return counts


def blockers_of(db: Session, item_id: int) -> list[m.Item]:
    return list(db.scalars(
        select(m.Item).join(m.Dependency, m.Dependency.blocker_item_id == m.Item.id)
        .where(m.Dependency.blocked_item_id == item_id)
    ))


def blocked_by_me(db: Session, item_id: int) -> list[m.Item]:
    return list(db.scalars(
        select(m.Item).join(m.Dependency, m.Dependency.blocked_item_id == m.Item.id)
        .where(m.Dependency.blocker_item_id == item_id)
    ))


def phase_budget(db: Session, phase) -> dict:
    if isinstance(phase, int):
        phase = db.get(m.Phase, phase)
    items = list(db.scalars(
        select(m.Item).where(m.Item.phase_id == phase.id).options(selectinload(m.Item.quotes))
    ))
    committed = 0
    spent = 0
    for it in items:
        spent += it.actual_cost or 0
        if it.status in NOT_ACTIVE:
            continue
        accepted = next((q for q in it.quotes if q.status == "accepted"), None)
        if accepted is not None:
            committed += accepted.amount or 0
        elif it.status in ("approved", "scheduled", "in_progress"):
            committed += it.estimate_high or 0
    budget_max, budget_min = phase.budget_max, phase.budget_min
    return {
        "budget_min": budget_min,
        "budget_max": budget_max,
        "committed": committed,
        "spent": spent,
        "available": budget_max - committed - spent if budget_max is not None else None,
        "available_vs_min": budget_min - committed - spent if budget_min is not None else None,
    }


def _sort_key(counts: dict[int, int]):
    rank = {c: i for i, c in enumerate(m.CATEGORIES)}

    def key(it: m.Item):
        return (
            rank.get(it.category, len(rank)),
            -counts.get(it.id, 0),
            it.estimate_high if it.estimate_high is not None else float("inf"),
        )
    return key


def next_actions(db: Session, limit: int = 10) -> list[dict]:
    blocked = blocked_ids(db)
    counts = blocks_count(db)
    nq = needs_quotes(db)
    items = db.scalars(
        select(m.Item).where(m.Item.status.notin_(("done", "parked", "archived", "proposed")))
    ).all()
    items = [it for it in items if it.id not in blocked]
    items.sort(key=_sort_key(counts))

    phase_cache: dict[int, dict] = {}
    out = []
    for it in items[:limit]:
        over_budget = False
        if it.phase_id and it.phase and it.phase.budget_max is not None and it.estimate_low is not None:
            if it.phase_id not in phase_cache:
                phase_cache[it.phase_id] = phase_budget(db, it.phase)
            available = phase_cache[it.phase_id]["available"]
            if available is not None and it.estimate_low > available:
                over_budget = True
        out.append({
            "item": it,
            "blocks": counts.get(it.id, 0),
            "over_budget": over_budget,
            "needs_quotes": nq.get(it.id),
        })
    return out


def needs_quotes(db: Session) -> dict[int, int]:
    threshold = int(services.get_setting(db, "quote_threshold", "100000"))
    items = db.scalars(
        select(m.Item).where(m.Item.status.notin_(NOT_ACTIVE)).options(selectinload(m.Item.quotes))
    )
    out = {}
    for it in items:
        if it.estimate_high is None or it.estimate_high < threshold:
            continue
        count = sum(1 for q in it.quotes if q.status in ("received", "accepted"))
        if count < 3:
            out[it.id] = count
    return out


def timeline_warnings(db: Session) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    items = db.scalars(select(m.Item).where(m.Item.planned_start.isnot(None)))
    for it in items:
        warnings = []
        for blocker in blockers_of(db, it.id):
            if blocker.planned_end and blocker.planned_end > it.planned_start:
                warnings.append(
                    f"{blocker.title} is not planned to finish until {blocker.planned_end.isoformat()}, "
                    f"after this item's start"
                )
        if warnings:
            out[it.id] = warnings
    return out


def savings_pot(db: Session) -> int:
    return db.scalar(select(func.coalesce(func.sum(m.SavingsEntry.amount), 0))) or 0


def _add_months(d: date, n: int) -> date:
    month0 = d.month - 1 + n
    return date(d.year + month0 // 12, month0 % 12 + 1, 1)


def savings_projection(db: Session) -> dict:
    pot = savings_pot(db)
    monthly = int(services.get_setting(db, "monthly_saving_target", "0"))
    later = db.scalar(select(m.Phase).where(m.Phase.name == "Later"))

    queue_items: list[m.Item] = []
    if later:
        counts = blocks_count(db)
        queue_items = list(db.scalars(
            select(m.Item).where(m.Item.phase_id == later.id, m.Item.status.notin_(NOT_ACTIVE))
        ))
        queue_items.sort(key=_sort_key(counts))

    start_month = date(date.today().year, date.today().month, 1)
    cumulative = 0
    queue = []
    for it in queue_items:
        cost = it.estimate_high or it.estimate_low or 0
        cumulative += cost
        affordable_month = None
        if monthly > 0 or pot >= cumulative:
            for n in range(241):
                if pot + monthly * n >= cumulative:
                    affordable_month = _add_months(start_month, n)
                    break
        queue.append({"item": it, "cost": cost, "affordable_month": affordable_month})

    return {"pot": pot, "monthly": monthly, "queue": queue}


def expiring_quotes(db: Session, days: int = 14) -> list[m.Quote]:
    today = date.today()
    end = today + timedelta(days=days)
    return list(db.scalars(
        select(m.Quote)
        .where(m.Quote.status.in_(("requested", "received")), m.Quote.valid_until.isnot(None))
        .where(m.Quote.valid_until >= today, m.Quote.valid_until <= end)
        .order_by(m.Quote.valid_until)
    ))
