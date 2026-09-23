import pytest

from app import models as m, services
from app.services import Actor, ServiceError, link_items, would_cycle


def make_item(db, title, **kw):
    return services.create(db, Actor(user_id=1), m.Item, title=title, **kw)


def test_self_link_rejected(db):
    a = make_item(db, "A")
    with pytest.raises(ServiceError):
        link_items(db, Actor(user_id=1), a.id, a.id)


def test_direct_cycle_rejected(db):
    a = make_item(db, "A")
    b = make_item(db, "B")
    link_items(db, Actor(user_id=1), a.id, b.id)
    with pytest.raises(ServiceError):
        link_items(db, Actor(user_id=1), b.id, a.id)


def test_transitive_cycle_rejected(db):
    a = make_item(db, "A")
    b = make_item(db, "B")
    c = make_item(db, "C")
    link_items(db, Actor(user_id=1), a.id, b.id)
    link_items(db, Actor(user_id=1), b.id, c.id)
    with pytest.raises(ServiceError):
        link_items(db, Actor(user_id=1), c.id, a.id)


def test_valid_dag_allowed(db):
    a = make_item(db, "A")
    b = make_item(db, "B")
    c = make_item(db, "C")
    link_items(db, Actor(user_id=1), a.id, b.id)
    link_items(db, Actor(user_id=1), a.id, c.id)
    link_items(db, Actor(user_id=1), b.id, c.id)
    assert db.query(m.Dependency).count() == 3
    graph = services.blockers_map(db)
    assert would_cycle(graph, c.id, a.id) is True
    assert would_cycle(graph, a.id, c.id) is False
