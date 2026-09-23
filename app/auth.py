import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m

_ph = PasswordHasher()
_failures: dict[str, list[float]] = defaultdict(list)
MAX_FAILURES, WINDOW, LOCKOUT = 10, 900, 900
# Verified when the username is unknown so a miss costs the same Argon2 time as a wrong password.
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def locked_out(key: str) -> bool:
    recent = [t for t in _failures.get(key, ()) if time.time() - t < WINDOW]
    if not recent:
        _failures.pop(key, None)
        return False
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES and time.time() - recent[-1] < LOCKOUT


def authenticate(db: Session, username: str, password: str, key: str) -> m.User | None:
    """key identifies the client for rate limiting, e.g. f'{ip}:{username}'. A per-username key also applies,
    since forwarded client IPs can be spoofed or collapse to the proxy address."""
    username = username.strip().lower()
    if len(username) > 150:  # bounds the in-memory failure table on the public login
        return None
    keys = (key, f"user:{username}")
    if any(locked_out(k) for k in keys):
        return None
    user = db.scalar(select(m.User).where(m.User.username == username))
    try:
        if _ph.verify(user.password_hash if user else _DUMMY_HASH, password) and user:
            _failures.pop(key, None)
            return user
    except VerificationError:
        pass
    for k in keys:
        _failures[k].append(time.time())
    return None
