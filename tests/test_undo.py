from app import models as m, services
from app.services import Actor


def test_undo_accept_quote_reverts_whole_batch(db):
    a = Actor(user_id=None)
    item = services.create(db, a, m.Item, title="Boiler", category="urgent", status="quoting")
    q1, q2, q3 = (services.create(db, a, m.Quote, item_id=item.id, amount=100 * i, status="received") for i in (1, 2, 3))
    services.accept_quote(db, a, q1)
    assert (q1.status, q2.status, q3.status, item.status) == ("accepted", "declined", "declined", "approved")

    row = db.query(m.ActivityLog).filter_by(entity="quote", entity_id=q1.id, action="update").one()
    services.undo(db, a, row.id)
    assert (q1.status, q2.status, q3.status, item.status) == ("received", "received", "received", "quoting")
