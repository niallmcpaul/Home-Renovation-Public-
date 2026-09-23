# Renovation Tracker

Spec: `PLAN.md`. Current state and next steps: `HANDOVER.md`.

- Python, FastAPI, SQLAlchemy 2, SQLite (WAL), Jinja2 + HTMX, MCP Python SDK 2.2 (`mcp.server.mcpserver.MCPServer`; there is no `FastMCP` in 2.x).
- Money is integer pence everywhere. Convert at the edges only (`app/web/common.py: money, to_pence`).
- Every write goes through `app/services.py` (`create`/`update`/`delete`/`link_items`/`accept_quote`) with an `Actor(user_id, via)`, so it lands in `activity_log`. Never write models directly from routes or MCP tools.
- Derived figures (blocked, committed, next actions, projections) live in `app/planning.py`; do not recompute them in templates.
- Two listeners: private UI (`app.main.build_private_app`) and public MCP/OAuth (`app.public.build_public_app`). Nothing UI-related may be mounted on the public app; `tests/test_public.py` enforces this.
- Tests only for cycle detection, budget/next-action logic, and the public listener's auth boundary. Run `.venv/bin/pytest -q`.
- Concise, functional code; no comment clutter.
