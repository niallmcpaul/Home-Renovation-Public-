import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m

_ph = PasswordHasher()
_failures: dict[str, list[float]] = defaultdict(list)
MAX_FAILURES, WINDOW, LOCKOUT = 10, 900, 900


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def locked_out(key: str) -> bool:
    recent = [t for t in _failures[key] if time.time() - t < WINDOW]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES and time.time() - recent[-1] < LOCKOUT


def authenticate(db: Session, username: str, password: str, key: str) -> m.User | None:
    """key identifies the client for rate limiting, e.g. f'{ip}:{username}'. A per-username key also applies,
    since forwarded client IPs can be spoofed or collapse to the proxy address."""
    username = username.strip().lower()
    keys = (key, f"user:{username}")
    if any(locked_out(k) for k in keys):
        return None
    user = db.scalar(select(m.User).where(m.User.username == username))
    try:
        if user and _ph.verify(user.password_hash, password):
            _failures.pop(key, None)
            return user
    except VerifyMismatchError:
        pass
    for k in keys:
        _failures[k].append(time.time())
    return None
