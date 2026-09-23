# Handover: Renovation Tracker build sprint (23 Sep 2026)

Built in one 35-minute cloud session (15:16-15:50 BST) against `PLAN.md` (the original build plan, copied into the repo). This file records what exists, what was verified, what was not, and where to pick up.

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
| `app/services.py` | Every write goes through `create`/`update`/`delete`; each logs to `activity_log`. Cycle-checked `link_items`, `accept_quote`, `choose_option`, optimistic-concurrency check, `undo`. Multi-record actions (accept quote, choose option, confirm all) share a `batch_id` and undo as one step |
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
| 3. Planning | Done, including a CSS Gantt on the timeline and a "choose this option" action for decision groups (web and MCP) | Unit tests for cycles, budget, next actions, quote nudge, savings projection; `.ics` feed checked for CRLF and escaping; Gantt checked by screenshot at 390px |
| 4. MCP | Done; not yet tried from claude.ai | `tests/test_public.py` runs the full OAuth flow (DCR, PKCE, login, code exchange, refresh rotation, revoke) then `tools/list` and `create_items`. Separately, a live test over real HTTP with the official MCP client imported 5 defects from `docs/mock-survey.md` and confirmed they appear in the dashboard inbox (19/19 steps) |
| 5. Networking | Not started: needs your PC | README steps 5-6 cover Tailscale, Funnel and the connector |
| 6. Operations | Done except a real restore test on your machine | Docker image built and run here: `init`, `seed` and `backup` work in the container, UI answers on 8000, `/mcp` returns 401 without a token, the public port serves no UI. Backup/restore round trip tested; seed loads 24 items and 22 dependencies |

## Known gaps and decisions taken without you

- **OAuth paths differ from PLAN §3.** MCP SDK 2.2 fixes them at `/authorize`, `/token`, `/register`, `/revoke`, not under `/oauth/`. Discovery metadata advertises the real paths, so Claude finds them. The login page is `/oauth/login`.
- **MCP SDK 2.x API.** `FastMCP` no longer exists; the server uses `mcp.server.mcpserver.MCPServer`. `requirements.txt` pins `mcp~=2.2.0` because this area changes between releases.
- **DCR client secrets are stored in plain text** (the SDK compares them directly). Access and refresh tokens and auth codes are stored as SHA-256 hashes, per PLAN §9.
- **Rate limiting behind Funnel.** The public listener trusts `X-Forwarded-For` from any source, which a caller can forge. Lockout therefore also applies per username (10 failures in 15 minutes locks that username for 15 minutes). The side effect: someone with the public URL could lock a householder out of the Claude connector for 15 minutes. The web UI on the tailnet uses the same counter. Check what headers Funnel actually sets and narrow `forwarded_allow_ips` if possible.
- **Host check on `/mcp`.** Only the host in `PUBLIC_BASE_URL` (plus localhost) is accepted, so `PUBLIC_BASE_URL` must match the Funnel URL exactly or every call is refused.
- **Seed phase assignment (assumption).** Loft, all garage items, solar, external wall insulation and kitchen refit are in "Later"; everything else is in "Phase 1". The garage Lawful Development Certificate and CCTV drainage survey are cheap enabling tasks and arguably belong in Phase 1; move them in the UI if you agree.
- **Repaint estimate** seeded as £3,450–£7,500 (labour plus 15–25% materials), with the raw wording in the description.
- **Schema management.** The app runs `alembic upgrade head` on every start (a fresh database is created and stamped instead), so pulling new code and restarting is enough. Model changes need an Alembic migration (`alembic revision --autogenerate`). The live test found this gap: before the fix, an existing database missed a new column and every MCP write failed.
- **MCP error reporting.** Unexpected server errors now return their type and message to Claude, labelled as server-side, instead of a bare "Error executing tool".
- **No CSRF tokens** on the private UI; it relies on `SameSite=Lax` cookies and tailnet-only reach, as PLAN §9 specifies.
- **HEIC photos** (iPhone default) are converted to JPEG on upload via `pillow-heif`, so every browser can show them.
- **Backups run in a second container** on a 24-hour sleep loop, not at a fixed time of night. On Windows, keep the repo inside the WSL2 filesystem: SQLite WAL on a Windows-mounted folder is unreliable.
- **The `data` and `rclone` folders ship in the repo (empty)**. If Docker creates `./data` itself it is owned by root and the container's non-root user cannot write the database (`unable to open database file`). This happened in testing. If you hit it on Linux: `sudo chown -R 1000:1000 data rclone`.
- **rclone in the image is untested here.** The Dockerfile installs Debian's `rclone` package, but this sandbox could not reach the Debian mirrors to confirm. If `RCLONE_REMOTE` is set and the copy fails, the backup still completes locally and prints a warning.
- Tests pass on Python 3.11 locally and 3.12 in GitHub Actions.

## Added beyond the plan

- **Grouped undo:** actions that change several records undo together (e.g. "Undo (4 changes)" after accepting a quote). Uses the second Alembic migration (`b7c1d2e3f4a5`), applied automatically at startup.
- **Confirm all** button on the dashboard's "Proposed by Claude" inbox, for survey imports.
- **`choose_option`** for decision groups: parks the alternatives. In the web UI and as an MCP tool.
- MCP `list_items` accepts `source`, and the server instructions tell Claude to check for an earlier import before importing a survey, to avoid duplicates.

## Review passes run at the end of the sprint

- **Security (public side):** fixed username enumeration via Argon2 timing, unbounded growth of the lockout table, a client-name spoofing risk on the OAuth sign-in page (it now shows the redirect host), upload serving headers (`nosniff`, CSP sandbox), and hardened the login `next` redirect. Confirmed sound: exact redirect-URI matching, PKCE, single-use codes and refresh tokens, hashed expiring tokens, no hard delete over MCP. Not changed: open DCR (Claude needs it); no CSRF token on the OAuth login form (a password is always required).
- **Correctness (private side):** items blocked only by archived items no longer count as blocked; accepting a quote now advances the item to `approved`; removed N+1 queries in budget and quote-nudge calculations; savings projection table readable on a phone.

## Still to resolve with the owners (PLAN §15)

1. PC operating system and whether it can stay on (Windows needs Docker Desktop with WSL2).
2. Domain ownership (only matters for Cloudflare Tunnel instead of Funnel).
3. Backup destination for `rclone` (set `RCLONE_REMOTE`).
4. Usernames/display names; whether both householders have Claude accounts.
5. Quote threshold (seeded £1,000) and monthly saving target (seeded £0).

## Suggested next session

1. On the PC: follow README steps 1-4, create both accounts, open the UI over Tailscale from a phone.
2. Build the image on the PC (`docker compose build`). It built and ran in the sandbox before the `rclone` install line was added; that line is unverified.
3. Optional: MCP Inspector against `http://localhost:8001/mcp` (`npx @modelcontextprotocol/inspector`) to browse the tools by hand.
4. Set up Funnel on port 8001, set `PUBLIC_BASE_URL`, restart, and add the connector in claude.ai.
5. End-to-end test: upload `docs/mock-survey.md` (a fictional survey written for this purpose) to a Claude conversation and ask Claude to import the defects. They should appear in the dashboard's "Proposed by Claude" inbox.
6. Run a real backup and restore test (README "Backups").
