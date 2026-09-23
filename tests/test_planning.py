from datetime import date

from app import models as m, planning, services
from app.services import Actor


def mk_item(db, **kw):
    kw.setdefault("category", "wish")
    kw.setdefault("status", "idea")
    return services.create(db, Actor(user_id=None), m.Item, **kw)


def test_phase_budget_committed_spent_available(db):
    phase = services.create(db, Actor(user_id=None), m.Phase, name="Phase 1", budget_min=3000, budget_max=5000)
    approved = mk_item(db, title="Approved", phase_id=phase.id, status="approved", estimate_high=2000)
    quoted = mk_item(db, title="Quoted", phase_id=phase.id, status="quoting", estimate_high=9999)
    services.create(db, Actor(user_id=None), m.Quote, item_id=quoted.id, amount=1500, status="accepted")
    done = mk_item(db, title="Done", phase_id=phase.id, status="done", actual_cost=500)
    mk_item(db, title="Parked", phase_id=phase.id, status="parked", estimate_high=999)

    result = planning.phase_budget(db, phase)
    assert result["committed"] == 3500  # 2000 (approved estimate) + 1500 (accepted quote)
    assert result["spent"] == 500
    assert result["available"] == 5000 - 3500 - 500
    assert result["available_vs_min"] == 3000 - 3500 - 500


def test_next_actions_ordering_and_blocked_exclusion(db):
    x = mk_item(db, title="X", category="wish")
    u2 = mk_item(db, title="U2", category="urgent", estimate_high=100)
    services.link_items(db, Actor(user_id=None), u2.id, x.id)
    u1 = mk_item(db, title="U1", category="urgent", estimate_high=100)
    e1 = mk_item(db, title="E1", category="enabling", estimate_high=300)
    i1 = mk_item(db, title="I1", category="improvement")

    blocker = mk_item(db, title="Blocker", category="urgent")
    blocked = mk_item(db, title="Blocked", category="urgent")
    services.link_items(db, Actor(user_id=None), blocker.id, blocked.id)

    mk_item(db, title="Done", category="urgent", status="done")
    mk_item(db, title="Parked", category="urgent", status="parked")
    mk_item(db, title="Proposed", category="urgent", status="proposed")

    actions = planning.next_actions(db, limit=20)
    ids = [a["item"].id for a in actions]

    assert blocked.id not in ids
    assert all(a["item"].status not in ("done", "parked", "proposed") for a in actions)

    index = {i: pos for pos, i in enumerate(ids)}
    assert index[u2.id] < index[u1.id] < index[e1.id] < index[i1.id]
    u2_entry = next(a for a in actions if a["item"].id == u2.id)
    assert u2_entry["blocks"] == 1


def test_needs_quotes(db):
    services.set_setting(db, "quote_threshold", "1000")
    high = mk_item(db, title="High", estimate_high=1500)
    quoted_enough = mk_item(db, title="QuotedEnough", estimate_high=2000)
    for _ in range(3):
        services.create(db, Actor(user_id=None), m.Quote, item_id=quoted_enough.id, amount=100, status="received")
    mk_item(db, title="Low", estimate_high=500)
    mk_item(db, title="DoneHigh", estimate_high=5000, status="done")

    result = planning.needs_quotes(db)
    assert result == {high.id: 0}


def test_savings_projection_months(db):
    services.create(db, Actor(user_id=None), m.Phase, name="Later")
    services.set_setting(db, "monthly_saving_target", "500")
    services.create(db, Actor(user_id=None), m.SavingsEntry, date=date.today().isoformat(), amount=1000)
    later = db.query(m.Phase).filter_by(name="Later").one()
    first = mk_item(db, title="First", phase_id=later.id, estimate_high=1000)
    second = mk_item(db, title="Second", phase_id=later.id, estimate_high=2000)

    proj = planning.savings_projection(db)
    assert proj["pot"] == 1000
    assert proj["monthly"] == 500
    start = date(date.today().year, date.today().month, 1)
    assert proj["queue"][0]["item"].id == first.id
    assert proj["queue"][0]["affordable_month"] == start
    assert proj["queue"][1]["item"].id == second.id
    assert proj["queue"][1]["affordable_month"] == planning._add_months(start, 4)
