import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import auth, config, db as dbm, models as m
from app.db import init_db, make_engine

BASE = config.PUBLIC_BASE_URL
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'pub.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(dbm, "SessionLocal", Session)
    with Session() as db:
        db.add(m.User(username="alice", display_name="Alice", password_hash=auth.hash_password("pw-alice")))
        db.add(m.Room(name="Kitchen"))
        db.commit()
    from app.public import build_public_app
    with TestClient(build_public_app(), base_url=BASE, follow_redirects=False) as c:
        yield c


def test_unauthenticated_mcp_rejected(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_metadata(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["registration_endpoint"] and meta["revocation_endpoint"]
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        prm = client.get(path).json()
        assert prm["resource"] == f"{BASE}/mcp"


@pytest.mark.parametrize("path", ["/", "/items", "/items/1", "/uploads/x", "/login", "/static/app.css", "/api/items"])
def test_no_ui_routes(client, path):
    assert client.get(path).status_code == 404


def _sse_json(r):
    if r.headers["content-type"].startswith("application/json"):
        return r.json()
    return json.loads(next(line[5:] for line in r.text.splitlines() if line.startswith("data:")))


def _authorize(client, cid, challenge, password="pw-alice"):
    r = client.get("/authorize", params={
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "state": "xyz",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{BASE}/mcp"})
    assert r.status_code == 302 and "/oauth/login" in r.headers["location"]
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    page = client.get("/oauth/login", params={"req": req})
    assert page.status_code == 200 and "Claude" in page.text
    return client.post("/oauth/login", data={"req": req, "username": "alice", "password": password})


def test_full_oauth_flow_and_tools(client):
    reg = client.post("/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT],
                                         "token_endpoint_auth_method": "none",
                                         "grant_types": ["authorization_code", "refresh_token"]})
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    assert _authorize(client, cid, challenge, password="wrong").status_code == 401
    r = _authorize(client, cid, challenge)
    assert r.status_code == 302 and r.headers["location"].startswith(REDIRECT)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["xyz"]
    code = q["code"][0]

    bad = client.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                      "redirect_uri": REDIRECT, "code_verifier": "wrong" * 10})
    assert bad.status_code == 400
    tok = client.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                      "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    tokens = tok.json()
    assert tokens["expires_in"] <= 3600 and tokens["refresh_token"]
    replay = client.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                         "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert replay.status_code == 400

    with dbm.SessionLocal() as db:
        stored = {t.token_hash for t in db.query(m.OAuthToken)}
    assert tokens["access_token"] not in stored and hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored

    h = {"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json, text/event-stream"}
    init = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
    assert init.status_code == 200, init.text
    assert "proposed" in _sse_json(init)["result"]["instructions"]
    sid = init.headers.get("mcp-session-id")
    if sid:
        h["mcp-session-id"] = sid
    h["mcp-protocol-version"] = _sse_json(init)["result"]["protocolVersion"]
    client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    tools = _sse_json(client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    names = {t["name"] for t in tools["result"]["tools"]}
    assert {"list_items", "get_item", "create_items", "archive_item", "next_actions"} <= names

    call = _sse_json(client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "create_items", "arguments": {"items": [{"title": "Damp in cellar", "category": "urgent",
                                                         "source": "survey", "source_ref": "D4"}]}}}))
    assert not call["result"].get("isError"), call
    with dbm.SessionLocal() as db:
        item = db.query(m.Item).one()
        log = db.query(m.ActivityLog).filter_by(entity="item").one()
    assert (item.status, item.source, item.created_by) == ("proposed", "survey", 1)
    assert (log.via, log.user_id) == ("claude", 1)

    ref = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                      "client_id": cid})
    assert ref.status_code == 200 and ref.json()["refresh_token"] != tokens["refresh_token"]
    reuse = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                        "client_id": cid})
    assert reuse.status_code == 400

    from app import oauth
    with dbm.SessionLocal() as db:
        assert oauth.list_clients(db)[0]["client_name"] == "Claude"
        oauth.revoke_client(db, cid)
    r = client.post("/mcp", headers={**h, "Authorization": f"Bearer {ref.json()['access_token']}"},
                    json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    assert r.status_code == 401


def test_oauth_login_escapes_shows_destination_and_locks_out(client, monkeypatch):
    from collections import defaultdict
    monkeypatch.setattr(auth, "_failures", defaultdict(list))
    evil = "https://evil.example/cb"
    reg = client.post("/register", json={"client_name": "<script>alert(1)</script>", "redirect_uris": [evil],
                                         "token_endpoint_auth_method": "none"})
    cid = reg.json()["client_id"]
    r = client.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": evil,
                                         "code_challenge": "x" * 43, "code_challenge_method": "S256"})
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    page = client.get("/oauth/login", params={"req": req}).text
    assert "<script>" not in page and "&lt;script&gt;" in page and "evil.example" in page
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/oauth/login", data={"req": req, "username": "alice", "password": "no"}).status_code == 401
    assert client.post("/oauth/login", data={"req": req, "username": "alice", "password": "pw-alice"}).status_code == 401
