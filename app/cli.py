import getpass
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app import config, models as m
from app.auth import hash_password
from app.backup import backup, export_json, restore
from app.db import SessionLocal, init_db
from app.seed import seed as seed_db

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", config.DB_URL)
    return cfg


def cmd_init():
    init_db()
    command.stamp(_alembic_config(), "head")
    print("Database initialised and stamped at head.")


def cmd_seed():
    db = SessionLocal()
    try:
        n = seed_db(db)
        db.commit()
        print(f"Seeded {n} items." if n else "Already seeded, skipped.")
    finally:
        db.close()


def cmd_adduser(username: str, display_name: str):
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        sys.exit("Passwords do not match.")
    if len(pw1) < 8:
        sys.exit("Password must be at least 8 characters.")
    db = SessionLocal()
    try:
        username = username.strip().lower()
        if db.scalar(select(m.User).where(m.User.username == username)):
            sys.exit(f"User '{username}' already exists.")
        db.add(m.User(username=username, display_name=display_name, password_hash=hash_password(pw1)))
        db.commit()
        print(f"User '{username}' created.")
    finally:
        db.close()


def cmd_passwd(username: str):
    pw1 = getpass.getpass("New password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        sys.exit("Passwords do not match.")
    if len(pw1) < 8:
        sys.exit("Password must be at least 8 characters.")
    db = SessionLocal()
    try:
        username = username.strip().lower()
        user = db.scalar(select(m.User).where(m.User.username == username))
        if not user:
            sys.exit(f"User '{username}' not found.")
        user.password_hash = hash_password(pw1)
        db.commit()
        print(f"Password updated for '{username}'.")
    finally:
        db.close()


def cmd_backup():
    dest = config.DATA_DIR / "backups"
    archive = backup(dest)
    print(f"Backup written to {archive}")


def cmd_restore(path: str):
    restore(path)
    print(f"Restored from {path}. Restart the app container now.")


def cmd_export_json(path: str):
    db = SessionLocal()
    try:
        data = export_json(db)
    finally:
        db.close()
    Path(path).write_text(json.dumps(data, indent=2, default=str))
    print(f"Exported to {path}")


COMMANDS = {
    "init": (cmd_init, 0),
    "seed": (cmd_seed, 0),
    "adduser": (cmd_adduser, 2),
    "passwd": (cmd_passwd, 1),
    "backup": (cmd_backup, 0),
    "restore": (cmd_restore, 1),
    "export-json": (cmd_export_json, 1),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m app.cli <{'|'.join(COMMANDS)}> [args]")
        sys.exit(1)
    fn, nargs = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if len(args) != nargs:
        sys.exit(f"'{sys.argv[1]}' needs {nargs} argument(s), got {len(args)}.")
    fn(*args)


if __name__ == "__main__":
    main()
