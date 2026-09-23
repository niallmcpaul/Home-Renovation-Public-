from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models as m
from app.db import get_db
from app.services import Actor

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def money(pence: int | None) -> str:
    return "–" if pence is None else f"£{pence / 100:,.0f}" if pence % 100 == 0 else f"£{pence / 100:,.2f}"


def to_pence(value: str | None) -> int | None:
    value = (value or "").replace("£", "").replace(",", "").strip()
    return round(float(value) * 100) if value else None


templates.env.filters["money"] = money
templates.env.globals.update(STATUSES=m.STATUSES, CATEGORIES=m.CATEGORIES, SIZES=m.SIZES,
                             QUOTE_STATUSES=m.QUOTE_STATUSES, ATTACHMENT_KINDS=m.ATTACHMENT_KINDS)


class LoginRequired(Exception):
    pass


def require_user(request: Request, db: Session = Depends(get_db)) -> m.User:
    uid = request.session.get("user_id")
    user = db.get(m.User, uid) if uid else None
    if not user:
        raise LoginRequired()
    return user


def actor(user: m.User) -> Actor:
    return Actor(user_id=user.id, via="web")


def render(request: Request, name: str, user: m.User | None = None, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx}, status_code=status_code)


def get_or_404(db: Session, cls, id_: int):
    obj = db.get(cls, id_)
    if not obj:
        raise HTTPException(404)
    return obj
