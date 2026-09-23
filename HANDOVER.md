# Handover: Renovation Tracker build sprint (23 Sep 2026)

Built in one 45-minute cloud session against `PLAN.md` (the original build plan, copied into the repo). This file records what exists, what was verified, what was not, and where to pick up.

## Getting this onto your machine

The cloud session could not push to GitHub (the Claude GitHub App has no access to `niallmcpaul/Home-Renovation-Public-`; fix at https://claude.ai/connect-github). The work is delivered two ways:

- `renovation-tracker.bundle`: a git bundle with full history of branch `claude/weekly-usage-sprint-handover-epdq1h`. In your local clone:
  ```
  git fetch /path/to/renovation-tracker.bundle claude/weekly-usage-sprint-handover-epdq1h:sprint
  git checkout sprint
  ```
- `renovation-tracker.tar.gz`: plain source snapshot, if you would rather not use git.

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

STATUS_TABLE

## Known gaps and decisions taken without you

GAPS

## Still to resolve with the owners (PLAN §15)

1. PC operating system and whether it can stay on (Windows needs Docker Desktop with WSL2).
2. Domain ownership (only matters for Cloudflare Tunnel instead of Funnel).
3. Backup destination for `rclone` (set `RCLONE_REMOTE`).
4. Usernames/display names; whether both householders have Claude accounts.
5. Quote threshold (seeded £1,000) and monthly saving target (seeded £0).

## Suggested next session

NEXT
