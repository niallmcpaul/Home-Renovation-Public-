# Handover: Renovation Tracker build sprint (23 Sep 2026)

Built in one 45-minute cloud session against `PLAN.md` (the original build plan, copied into the repo). This file records what exists, what was verified, what was not, and where to pick up.

## Getting this onto your machine

All work is on branch `claude/weekly-usage-sprint-handover-epdq1h`, draft PR #1.

```
git fetch origin claude/weekly-usage-sprint-handover-epdq1h
git checkout claude/weekly-usage-sprint-handover-epdq1h
```

## Run it locally (no Docker)

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export DATA_DIR=./data SECRET_KEY=dev
.venv/bin/python -m app.cli init
.venv/bin/python -m app.cli seed
.venv/bin/python -m app.cli adduser <name> "<Display Name>"
.venv/bin/python -m app.main          # UI on 127.0.0.1:8000, MCP/OAuth on 127.0.0.1:8001
.venv/bin/pytest -q
```

## Layout

| Path | Contents |
|---|---|
| `app/models.py` | All tables from PLAN §5, plus OAuth client/code/token tables and an `undone` flag on `activity_log` |
| `app/services.py` | Every write goes through `create`/`update`/`delete`; each logs to `activity_log`. Cycle-checked `link_items`, `accept_quote`, optimistic-concurrency check, one-step `undo` |
| `app/planning.py` | Derived logic from PLAN §6 (blocked, blocks count, committed/spent/available, next actions, quote nudge, timeline warnings, savings projection) |
| `app/web/` | Private listener UI: `account.py` (login), `items.py` (dashboard, items, item detail, quotes, uploads, notes), `pages.py` (contractors, timeline + `.ics`, budget/savings, activity + undo, settings) |
| `app/oauth.py`, `app/mcp_server.py`, `app/public.py` | Public listener: MCP Streamable HTTP at `/mcp` behind OAuth 2.1 (DCR, PKCE, hashed tokens) |
| `app/main.py` | Runs both listeners in one process |
| `app/cli.py`, `app/seed.py`, `app/backup.py` | Admin commands, PLAN §12 seed data, SQLite online-API backup with 14 daily / 12 monthly retention |
| `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md` | Deployment and owner-facing setup guide |

## Status against PLAN §13 milestones

| Milestone | State | Verified how |
|---|---|---|
| 1. Core | Done | Seeded DB; every UI page returns 200 at 390px width in headless Chromium with no JS errors |
| 2. Quotes and people | Done | Scripted TestClient runs: quote add/accept, contractor CRUD, photo upload with downscale, notes |
| 3. Planning | Done (timeline is a month-grouped list, no Gantt bars) | Unit tests for cycles, budget, next actions, quote nudge, savings projection; `.ics` feed checked for CRLF and escaping |
| 4. MCP | Done, not yet tested with MCP Inspector or claude.ai | `tests/test_public.py` runs the full OAuth flow (DCR, PKCE, login, code exchange, refresh rotation, revoke) then `tools/list` and `create_items` over `/mcp` |
| 5. Networking | Not started: needs your PC | README steps 5-6 cover Tailscale, Funnel and the connector |
| 6. Operations | Done except a real restore test on your machine | Backup/restore round trip tested in the container; seed loads 24 items and 22 dependencies |

## Known gaps and decisions taken without you

- **OAuth paths differ from PLAN §3.** MCP SDK 2.2 fixes them at `/authorize`, `/token`, `/register`, `/revoke`, not under `/oauth/`. Discovery metadata advertises the real paths, so Claude finds them. The login page is `/oauth/login`.
- **MCP SDK 2.x API.** `FastMCP` no longer exists; the server uses `mcp.server.mcpserver.MCPServer`. `requirements.txt` pins `mcp~=2.2.0` because this area changes between releases.
- **DCR client secrets are stored in plain text** (the SDK compares them directly). Access and refresh tokens and auth codes are stored as SHA-256 hashes, per PLAN §9.
- **Rate limiting behind Funnel.** The public listener trusts `X-Forwarded-For` from any source, which a caller can forge. Lockout therefore also applies per username (10 failures in 15 minutes locks that username for 15 minutes). The side effect: someone with the public URL could lock a householder out of the Claude connector for 15 minutes. The web UI on the tailnet uses the same counter. Check what headers Funnel actually sets and narrow `forwarded_allow_ips` if possible.
- **Host check on `/mcp`.** Only the host in `PUBLIC_BASE_URL` (plus localhost) is accepted, so `PUBLIC_BASE_URL` must match the Funnel URL exactly or every call is refused.
- **Seed phase assignment (assumption).** Loft, all garage items, solar, external wall insulation and kitchen refit are in "Later"; everything else is in "Phase 1". The garage Lawful Development Certificate and CCTV drainage survey are cheap enabling tasks and arguably belong in Phase 1; move them in the UI if you agree.
- **Repaint estimate** seeded as £3,450–£7,500 (labour plus 15–25% materials), with the raw wording in the description.
- **Schema management.** `app.cli init` runs `create_all` and stamps Alembic head. Future model changes need an Alembic migration (`alembic revision --autogenerate`).
- **No CSRF tokens** on the private UI; it relies on `SameSite=Lax` cookies and tailnet-only reach, as PLAN §9 specifies.
- **HEIC photos** (iPhone default) are accepted but not downscaled; stock Pillow cannot decode HEIC. Adding `pillow-heif` would fix it.
- **Backups run in a second container** on a 24-hour sleep loop, not at a fixed time of night. On Windows, keep the repo inside the WSL2 filesystem: SQLite WAL on a Windows-mounted folder is unreliable.
- **Timeline** is a month-grouped list with dependency warnings; no Gantt bars.
- Tests pass on Python 3.11 locally and 3.12 in GitHub Actions (the Docker image uses 3.12). The Docker image itself has not been built yet.

## Still to resolve with the owners (PLAN §15)

1. PC operating system and whether it can stay on (Windows needs Docker Desktop with WSL2).
2. Domain ownership (only matters for Cloudflare Tunnel instead of Funnel).
3. Backup destination for `rclone` (set `RCLONE_REMOTE`).
4. Usernames/display names; whether both householders have Claude accounts.
5. Quote threshold (seeded £1,000) and monthly saving target (seeded £0).

## Suggested next session

1. On the PC: follow README steps 1-4, create both accounts, open the UI over Tailscale from a phone.
2. Build the Docker image once (`docker compose build`) and fix anything 3.12-specific.
3. Run MCP Inspector against `http://localhost:8001/mcp` (`npx @modelcontextprotocol/inspector`) to check the OAuth flow and tools by hand.
4. Set up Funnel on port 8001, set `PUBLIC_BASE_URL`, restart, and add the connector in claude.ai.
5. End-to-end test: upload `docs/mock-survey.md` (a fictional survey written for this purpose) to a Claude conversation and ask Claude to import the defects. They should appear in the dashboard's "Proposed by Claude" inbox.
6. Run a real backup and restore test (README "Backups").
