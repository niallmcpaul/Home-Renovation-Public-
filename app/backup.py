import os
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import date
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app import config, models as m

EXCLUDE_TABLES = {"users", "oauth_clients", "oauth_codes", "oauth_tokens"}
KEEP_DAILY, KEEP_MONTHLY = 14, 12


def _db_path() -> Path:
    return config.DATA_DIR / "renovation.db"


def backup(dest_dir: Path | str) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"renovation-{date.today().isoformat()}"
    staging = dest_dir / f".{name}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    src = sqlite3.connect(str(_db_path()))
    dst = sqlite3.connect(str(staging / "renovation.db"))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    if config.UPLOAD_DIR.exists():
        shutil.copytree(config.UPLOAD_DIR, staging / "uploads")

    archive = dest_dir / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=name)
    shutil.rmtree(staging)

    _apply_retention(dest_dir)

    remote = os.environ.get("RCLONE_REMOTE")
    if remote:
        if not shutil.which("rclone"):
            print("WARNING: RCLONE_REMOTE is set but rclone is not installed; off-machine copy skipped")
        elif subprocess.run(["rclone", "copy", str(archive), remote]).returncode != 0:
            print(f"WARNING: rclone copy to {remote} failed; local backup is intact")

    return archive


def _apply_retention(dest_dir: Path):
    archives = sorted(dest_dir.glob("renovation-*.tar.gz"))
    dailies = archives[-KEEP_DAILY:] if len(archives) > KEEP_DAILY else archives
    monthlies = [a for a in archives if a.name[len("renovation-"):-len(".tar.gz")].endswith("-01")][-KEEP_MONTHLY:]
    keep = set(dailies) | set(monthlies)
    for a in archives:
        if a not in keep:
            a.unlink()


def restore(archive: Path | str):
    archive = Path(archive)
    with tarfile.open(archive, "r:gz") as tar:
        extract_dir = archive.parent / ".restore-tmp"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        tar.extractall(extract_dir, filter="data")
        inner = next(extract_dir.iterdir())

        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inner / "renovation.db", _db_path())

        if (inner / "uploads").exists():
            if config.UPLOAD_DIR.exists():
                shutil.rmtree(config.UPLOAD_DIR)
            shutil.copytree(inner / "uploads", config.UPLOAD_DIR)

        shutil.rmtree(extract_dir)


def export_json(db: Session) -> dict:
    out = {}
    for cls in (m.Room, m.Phase, m.Item, m.Dependency, m.Quote, m.Contractor, m.Attachment, m.Note,
                m.SavingsEntry, m.Setting, m.ActivityLog):
        table = inspect(cls).local_table.name
        if table in EXCLUDE_TABLES:
            continue
        cols = [c.key for c in inspect(cls).mapper.column_attrs]
        rows = []
        for obj in db.scalars(select(cls)):
            rows.append({k: (v.isoformat() if hasattr(v := getattr(obj, k), "isoformat") else v) for k in cols})
        out[table] = rows
    return out
