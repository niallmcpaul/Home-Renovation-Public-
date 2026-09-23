import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'renovation.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")
PRIVATE_HOST = os.getenv("PRIVATE_HOST", "127.0.0.1")
PRIVATE_PORT = int(os.getenv("PRIVATE_PORT", "8000"))
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "127.0.0.1")
PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", "8001"))
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ACCESS_TOKEN_TTL = 3600
