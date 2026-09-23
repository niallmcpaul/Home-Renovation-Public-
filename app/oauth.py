import hashlib
import html
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError, RefreshToken, TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from app import config, db as dbm, models as m
from app.auth import authenticate

RESOURCE_URL = f"{config.PUBLIC_BASE_URL}/mcp"
LOGIN_PATH = "/oauth/login"
CODE_TTL = 300
PENDING_TTL = 600
REFRESH_TTL = 90 * 24 * 3600
ACCESS_TTL = min(config.ACCESS_TOKEN_TTL, 3600)

_pending: dict[str, tuple[float, str, AuthorizationParams]] = {}


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime | None) -> int | None:
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt else None


def _session() -> Session:
    return dbm.SessionLocal()


def _purge_pending():
    now = time.time()
    for k in [k for k, (exp, *_) in _pending.items() if exp < now]:
        _pending.pop(k, None)


def _issue(db: Session, client_id: str, user_id: int, scopes: list[str], resource: str | None) -> OAuthToken:
    access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(48), time.time()
    common = dict(client_id=client_id, user_id=user_id, scopes=" ".join(scopes), resource=resource or RESOURCE_URL)
    db.add(m.OAuthToken(token_hash=_hash(access), kind="access", expires_at=_naive(now + ACCESS_TTL), **common))
    db.add(m.OAuthToken(token_hash=_hash(refresh), kind="refresh", expires_at=_naive(now + REFRESH_TTL), **common))
    db.commit()
    return OAuthToken(access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh,
                      scope=" ".join(scopes) or None)


def _live_token(db: Session, token: str, kind: str) -> m.OAuthToken | None:
    row = db.get(m.OAuthToken, _hash(token))
    if not row or row.kind != kind or row.revoked or (row.expires_at and row.expires_at < _naive(time.time())):
        return None
    return row


class DBOAuthProvider:
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with _session() as db:
            row = db.get(m.OAuthClient, client_id)
            return OAuthClientInformationFull.model_validate_json(row.client_info_json) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with _session() as db:
            db.merge(m.OAuthClient(client_id=client_info.client_id, client_info_json=client_info.model_dump_json()))
            db.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (RESOURCE_URL, config.PUBLIC_BASE_URL):
            raise AuthorizeError("invalid_target", "Unknown resource")
        _purge_pending()
        rid = secrets.token_urlsafe(32)
        _pending[rid] = (time.time() + PENDING_TTL, client.client_id, params)
        return f"{config.PUBLIC_BASE_URL}{LOGIN_PATH}?{urlencode({'req': rid})}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, code: str) -> AuthorizationCode | None:
        with _session() as db:
            row = db.get(m.OAuthCode, _hash(code))
            if not row or row.client_id != client.client_id:
                return None
            return AuthorizationCode(code=code, client_id=row.client_id, expires_at=_ts(row.expires_at),
                                     subject=str(row.user_id), **json.loads(row.data_json))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          code: AuthorizationCode) -> OAuthToken:
        with _session() as db:
            if not db.execute(delete(m.OAuthCode).where(m.OAuthCode.code_hash == _hash(code.code))).rowcount:
                raise TokenError("invalid_grant", "authorization code already used")
            return _issue(db, client.client_id, int(code.subject), code.scopes, code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with _session() as db:
            row = _live_token(db, refresh_token, "refresh")
            if not row or row.client_id != client.client_id:
                return None
            return RefreshToken(token=refresh_token, client_id=row.client_id, scopes=row.scopes.split(),
                                expires_at=_ts(row.expires_at), resource=row.resource, subject=str(row.user_id))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        with _session() as db:
            rotated = db.execute(update(m.OAuthToken).where(
                m.OAuthToken.token_hash == _hash(refresh_token.token), m.OAuthToken.revoked.is_(False),
            ).values(revoked=True)).rowcount
            if not rotated:
                raise TokenError("invalid_grant", "refresh token already used")
            return _issue(db, client.client_id, int(refresh_token.subject), scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        with _session() as db:
            row = _live_token(db, token, "access")
            if not row:
                return None
            return AccessToken(token=token, client_id=row.client_id, scopes=row.scopes.split(),
                               expires_at=_ts(row.expires_at), resource=row.resource, subject=str(row.user_id))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with _session() as db:
            row = db.get(m.OAuthToken, _hash(token.token))
            if row:
                db.execute(update(m.OAuthToken).where(
                    m.OAuthToken.client_id == row.client_id, m.OAuthToken.user_id == row.user_id,
                ).values(revoked=True))
                db.commit()


def complete_authorization(db: Session, rid: str, user_id: int) -> str | None:
    entry = _pending.pop(rid, None)
    if not entry or entry[0] < time.time():
        return None
    _, client_id, p = entry
    code = secrets.token_urlsafe(32)
    data = dict(scopes=p.scopes or [], code_challenge=p.code_challenge, redirect_uri=str(p.redirect_uri),
                redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly, resource=RESOURCE_URL)
    db.add(m.OAuthCode(code_hash=_hash(code), client_id=client_id, user_id=user_id, data_json=json.dumps(data),
                       expires_at=_naive(time.time() + CODE_TTL)))
    db.commit()
    return construct_redirect_uri(str(p.redirect_uri), code=code, state=p.state)


def list_clients(db: Session) -> list[dict]:
    now = _naive(time.time())
    active = dict(db.execute(
        select(m.OAuthToken.client_id, func.count()).where(
            m.OAuthToken.kind == "refresh", m.OAuthToken.revoked.is_(False), m.OAuthToken.expires_at > now,
        ).group_by(m.OAuthToken.client_id)
    ).all())
    out = []
    for c in db.scalars(select(m.OAuthClient).order_by(m.OAuthClient.created_at.desc())):
        info = json.loads(c.client_info_json)
        out.append({"client_id": c.client_id, "client_name": info.get("client_name") or "Unnamed client",
                    "redirect_uris": info.get("redirect_uris") or [], "created_at": c.created_at,
                    "active_grants": active.get(c.client_id, 0)})
    return out


def revoke_client(db: Session, client_id: str) -> None:
    db.execute(update(m.OAuthToken).where(m.OAuthToken.client_id == client_id).values(revoked=True))
    db.execute(delete(m.OAuthCode).where(m.OAuthCode.client_id == client_id))
    db.execute(delete(m.OAuthClient).where(m.OAuthClient.client_id == client_id))
    db.commit()


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in</title>
<style>body{{font-family:system-ui,sans-serif;max-width:22rem;margin:3rem auto;padding:0 1rem;color:#222}}
input,button{{width:100%;padding:.6rem;margin:.3rem 0;font-size:1rem;box-sizing:border-box}}
button{{background:#2d5a3d;color:#fff;border:0;border-radius:4px}}.err{{color:#b00020}}</style></head>
<body><h1>Renovation Tracker</h1><p><strong>{client}</strong> wants to read and edit your renovation data.</p>
{dest}{error}<form method="post"><input type="hidden" name="req" value="{req}">
<input name="username" placeholder="Username" autocomplete="username" required autofocus>
<input name="password" type="password" placeholder="Password" autocomplete="current-password" required>
<button>Sign in and allow</button></form></body></html>"""
_HEADERS = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


async def _client_name(client_id: str) -> str:
    client = await DBOAuthProvider().get_client(client_id)
    return (client and client.client_name) or "An application"


def _page(req: str, client: str, error: str = "", status: int = 200, dest: str = "") -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    # client_name is self-asserted via open registration; show where access is actually granted.
    dest = f"<p>Access will be sent to <strong>{html.escape(dest)}</strong>.</p>" if dest else ""
    body = _PAGE.format(client=html.escape(client), req=html.escape(req), error=err, dest=dest)
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


async def login_endpoint(request: Request):
    params = request.query_params if request.method == "GET" else await request.form()
    rid = str(params.get("req") or "")
    entry = _pending.get(rid)
    if not entry or entry[0] < time.time():
        return _page("", "Unknown", "This sign-in link has expired. Start the connection again from Claude.", 400)
    client = await _client_name(entry[1])
    dest = urlsplit(str(entry[2].redirect_uri)).netloc or str(entry[2].redirect_uri)
    if request.method == "GET":
        return _page(rid, client, dest=dest)
    username = str(params.get("username") or "")
    ip = request.client.host if request.client else "unknown"
    with _session() as db:
        user = authenticate(db, username, str(params.get("password") or ""), f"{ip}:{username.strip().lower()}")
        if not user:
            return _page(rid, client, "Invalid username or password, or too many attempts.", 401, dest)
        url = complete_authorization(db, rid, user.id)
    if not url:
        return _page("", client, "This sign-in link has expired. Start the connection again from Claude.", 400)
    return RedirectResponse(url, status_code=302, headers=_HEADERS)
