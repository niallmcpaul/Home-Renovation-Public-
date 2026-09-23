import pytest

from app.db import init_db, make_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
