from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db
from app.web.common import render

router = APIRouter()


def _safe_next(next: str | None) -> str:
    ok = next and next.startswith("/") and not next.startswith("//") and "\\" not in next \
        and not any(ord(c) < 0x21 for c in next)
    return next if ok else "/"


@router.get("/login")
def login_get(request: Request, next: str = "/"):
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", None, next=next)


@router.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
               db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{username.strip().lower()}"
    user = auth.authenticate(db, username, password, key)
    if user:
        request.session["user_id"] = user.id
        return RedirectResponse(_safe_next(next), status_code=303)
    msg = "Too many failed attempts. Try again later." if auth.locked_out(key) else "Invalid username or password."
    return render(request, "login.html", None, status_code=401, error=msg, next=next, username=username)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
