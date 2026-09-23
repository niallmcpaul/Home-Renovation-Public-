# Renovation Tracker: Build Plan

## 1. Purpose

A self-hosted web app for two householders to plan and track renovation of a Victorian end-terrace house (Robertson Road, Greenbank, Bristol). It holds everything from small wishes ("buy a mirror") to large projects ("loft conversion"), supports gathering and comparing quotes, models which work must precede other work, tracks budget across multi-year phases, and exposes an MCP server so Claude can read and edit the data conversationally (for example: import defects from a building survey PDF, change statuses, add quotes).

The owners will live in the house during works. Phase 1 budget is £10,000–£20,000; later phases are funded from savings or a possible future lump sum, one project at a time.

## 2. Decisions already made

| Decision | Choice | Reason |
|---|---|---|
| Hosting | Owner's existing PC, always on | Data stays at home |
| Access for humans | Tailscale (private network) | Phones and laptops reach the app anywhere without public exposure |
| Access for Claude | Remote MCP server, publicly reachable over HTTPS, OAuth-protected | Claude custom connectors connect from Anthropic's cloud, so VPN-only servers cannot be reached |
| Users | Two named accounts, concurrent use | Both householders edit from phone or laptop |
| Extras | Photos/receipts per item, contractor directory, timeline view, budget phases and savings tracking | Chosen by the owners |

## 3. Architecture

```
Phones / laptops ──Tailscale──▶ PC: web UI listener (tailnet only)
                                      │
                                      ├── app core (FastAPI) ── SQLite file + uploads folder
                                      │
Claude (Anthropic cloud) ──HTTPS──▶ PC: public listener (MCP + OAuth routes only)
                         via Tailscale Funnel (default) or Cloudflare Tunnel (alternative)
```

One codebase, one process, two listeners:

- **Private listener**: full web UI and JSON endpoints. Bound so that it is reachable only via the tailnet.
- **Public listener**: serves only `/mcp`, the OAuth endpoints (`/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`, `/oauth/register`, `/oauth/authorize`, `/oauth/token`) and the login page the OAuth flow needs. Nothing else is mounted on it.

Tailscale Funnel gives a public HTTPS URL on a `ts.net` name without owning a domain. Cloudflare Tunnel is the alternative if the owners hold a domain on Cloudflare. Both connect outbound from the PC, so no router ports are opened. Verify current Funnel behaviour (supported ports, path handling) against Tailscale's documentation before relying on it.

## 4. Tech stack

- Python 3.12, FastAPI, SQLAlchemy 2 (or SQLModel), Alembic migrations
- SQLite in WAL mode (sufficient for two users plus Claude)
- Server-rendered Jinja2 templates with HTMX; mobile-first CSS, no front-end build step
- Official MCP Python SDK (`mcp`), Streamable HTTP transport, mounted in the same ASGI app
- OAuth 2.1 authorisation server built into the app using the MCP SDK's auth provider interfaces: PKCE required, Dynamic Client Registration enabled (Claude supports DCR), short-lived access tokens with refresh tokens. Check the current SDK version's auth examples before implementing; this area changes between releases.
- Argon2 password hashing
- Docker Compose deployment with `restart: unless-stopped`

Coding standards requested by the owner: concise, functional code; no comment clutter; tests only where they protect real logic (see §13).

## 5. Data model

Money is stored as integer pence. Dates ISO 8601. Every table has `id`, `created_at`, `updated_at`, `created_by`, `updated_by`.

**users**: `username`, `display_name`, `password_hash`.

**rooms**: `name`, `sort_order`. Seed: Whole house, Exterior and roof, Front garden, Rear garden, Hall and stairs, Lounge, Dining room, Kitchen, Shower room, Bedroom 1, Bedroom 2, Bedroom 3, Study, Landing, Loft, Garage. Editable.

**phases**: `name`, `budget_min`, `budget_max`, `target_start`, `notes`. Seed: "Phase 1" (£10,000–£20,000), "Later".

**items**: the single list of everything.
- `title`, `description`
- `size`: `wish` | `task` | `project`
- `category`: `urgent` (safety, structural, survey defects) | `enabling` (must precede other work) | `improvement` | `wish`
- `status`: `proposed` | `idea` | `researching` | `quoting` | `approved` | `scheduled` | `in_progress` | `done` | `parked` | `archived`
- `room_id`, `phase_id` (nullable)
- `estimate_low`, `estimate_high`, `actual_cost`
- `planned_start`, `planned_end`, `completed_on`
- `needs_building_control` (bool), `needs_planning_check` (bool), `compliance_notes` (for example: Gas Safe engineer required, Part P electrical certificate, Lawful Development Certificate)
- `source`: `manual` | `claude` | `survey`; `source_ref` (for example survey section number)
- `decision_group` (nullable text): items sharing a group are mutually exclusive options, for example "Ground floor finish: sand existing boards vs new engineered oak"

**dependencies**: `blocker_item_id`, `blocked_item_id`. Reject cycles on insert.

**quotes**: `item_id`, `contractor_id`, `amount`, `includes_vat` (bool), `scope`, `exclusions`, `received_on`, `valid_until`, `status` (`requested` | `received` | `accepted` | `declined` | `expired`), `attachment_id`. Accepting one quote declines the others for that item after confirmation.

**contractors**: `name`, `company`, `trades`, `phone`, `email`, `website`, `recommended_by`, `registrations` (free text: Gas Safe, NICEIC, FENSA, TrustMark, etc.), `insurance_checked` (bool), `rating` (1–5), `notes`.

**attachments**: `item_id` or `quote_id`, `kind` (`photo` | `receipt` | `quote_pdf` | `certificate` | `other`), `filename`, `stored_path`, `mime_type`, `size_bytes`. Files live in `/data/uploads/` named by UUID. Images downscaled to max 2000 px on upload.

**notes**: `item_id`, `body`. Append-only discussion per item.

**savings_entries**: `date`, `amount` (positive = saved, negative = withdrawn), `note`. Plus **settings** keys `monthly_saving_target`, `quote_threshold` (default £1,000).

**activity_log**: `user_id`, `via` (`web` | `claude`), `entity`, `entity_id`, `action`, `before_json`, `after_json`, `at`. Every write goes through a service layer that records this. Used for the history view and one-step undo.

## 6. Derived logic

- **Blocked**: an item is blocked while any blocker is not `done`.
- **Blocks count**: number of non-done items an item blocks; shown as a badge.
- **Committed** per phase: sum of accepted quotes for non-done items in the phase, else the item's `estimate_high` if no quote is accepted and status ≥ `approved`.
- **Spent** per phase: sum of `actual_cost`.
- **Available**: `budget_max − committed − spent`, also shown against `budget_min`.
- **Next actions**: items not blocked, not done or parked, ordered by category (`urgent`, `enabling`, `improvement`, `wish`), then blocks count descending, then `estimate_high` ascending. Flag any whose `estimate_low` exceeds phase available budget.
- **Quote nudge**: items with `estimate_high` ≥ `quote_threshold` and fewer than three received quotes show "needs quotes (n/3)".
- **Timeline warnings**: an item planned to start before a blocker's planned end is flagged.
- **Savings projection**: current pot plus `monthly_saving_target` projected forward; show the month each unfunded "Later" item becomes affordable, in queue order.

## 7. Web UI (mobile-first)

1. **Dashboard**: Phase 1 budget bar (spent / committed / available), next actions (top 10), blocked items, "Proposed by Claude" inbox needing confirmation, quotes expiring within 14 days.
2. **Quick add**: a single text box on every page; Enter creates a `wish` in `idea` status. Everything else can be filled in later.
3. **Items list**: filter by status, category, room, phase, size; text search; sort options above.
4. **Item detail**: all fields, dependency editor (search-and-link), decision group siblings, quotes comparison table (amount, VAT, scope, exclusions, validity, contractor rating), attachments gallery with camera upload (`<input type="file" accept="image/*" capture>`), notes thread, history.
5. **Contractors**: list and detail with linked quotes and items.
6. **Timeline**: month-by-month list or simple Gantt of items with planned dates, dependency warnings inline. Optional `.ics` feed of planned dates for phone calendars.
7. **Budget**: phases with totals; savings entries and projection.
8. **Activity**: filterable log with undo on the most recent change per entity.

Concurrent editing: optimistic concurrency via `updated_at` check; on conflict show the other user's change and let the user reapply.

## 8. MCP server

Tools return compact JSON. No hard delete is exposed; archiving only. Every call is attributed to the authenticated user with `via = claude`.

| Tool | Purpose |
|---|---|
| `list_items(filters)` | status, category, room, phase, size, text, blocked-only |
| `get_item(id)` | full item with quotes, dependencies, attachment metadata, notes, recent history |
| `create_items(items[])` | batch create; defaults `status = proposed`, `source = claude`; accepts `source = survey` and `source_ref` |
| `update_item(id, fields)` | any editable field including status |
| `add_note(item_id, body)` | |
| `link_items(blocker_id, blocked_id)` / `unlink_items(...)` | rejects cycles with a clear message |
| `add_quote(...)` / `update_quote(...)` / `accept_quote(id)` | |
| `list_contractors(filter)` / `upsert_contractor(...)` | |
| `list_rooms()` / `list_phases()` | so Claude uses valid IDs |
| `budget_summary(phase_id?)` | committed, spent, available, savings pot, projection |
| `next_actions(limit?)` | as defined in §6 |
| `recent_activity(since?)` | |
| `archive_item(id)` | |

Survey workflow: the owner uploads the survey PDF into a Claude conversation; Claude extracts defects and recommendations and calls `create_items` with `category = urgent` where the surveyor flags safety or structural issues, `source = survey` and `source_ref` set to the survey section. Items arrive as `proposed` and appear in the dashboard inbox for a human to confirm or discard. No PDF parsing on the server.

Tool descriptions should state the status and category vocabularies and the meaning of `proposed`, so Claude uses them consistently.

## 9. Security

- Web UI listener reachable only on the tailnet; still requires login (session cookie, `Secure`, `HttpOnly`, `SameSite=Lax`).
- Public listener exposes only MCP and OAuth routes; login attempts rate-limited with lockout after repeated failures.
- OAuth: PKCE mandatory, redirect URIs restricted to those registered via DCR, tokens hashed at rest, access token lifetime ≤ 1 hour, refresh tokens revocable from a settings page listing connected clients.
- Uploads: size cap 20 MB, MIME sniffing, served only on the private listener.
- Secrets in `.env`, not committed.

## 10. Backups

Nightly job: SQLite online backup (`sqlite3 .backup` or the Python `sqlite3` backup API, not a file copy of a live database) plus the uploads folder, compressed and copied to an off-machine destination via `rclone`. Keep 14 daily and 12 monthly copies. A JSON export of all tables is also available from the settings page. Test a restore once during setup.

## 11. Deployment

- `docker-compose.yml` with the app container and a data volume at `/data` (database, uploads, backups staging).
- Tailscale installed on the host PC and on each phone and laptop; Funnel (or Cloudflare Tunnel) configured to point at the public listener port only.
- Host PC configured not to sleep; container auto-restarts; app starts on boot.
- Add the connector in claude.ai under Customize > Connectors using the public `/mcp` URL; each householder with a Claude account adds it and signs in as themselves. Connectors added on the web are then usable from the Claude mobile apps.
- README with plain step-by-step setup and recovery instructions; the owner can follow clear instructions but is not a developer.

## 12. Seed data

Estimates below are rough 2026 averages gathered during pre-purchase research, to be replaced by real quotes. All seeded as `status = idea` unless noted.

| Item | Size | Category | Room | Estimate | Blocked by |
|---|---|---|---|---|---|
| Import building survey findings | task | urgent | Whole house | – | – |
| Check Article 4 directions for the street with Bristol City Council | task | enabling | Whole house | – | – |
| Knock through reception rooms incl. chimney breast removal (structural engineer, Building Control) | project | enabling | Lounge | £6,000–£12,000 | Article 4 check |
| Ground-floor underfloor heating, between-joist wet system | project | enabling | Whole house | £5,700–£11,400 | Knock through |
| New engineered oak ground floor (decision group: ground floor finish) | project | improvement | Whole house | £3,000–£5,800 | Underfloor heating |
| Sand and varnish existing boards (decision group: ground floor finish) | task | improvement | Whole house | £700–£1,400 | – |
| Kitchen refit | project | improvement | Kitchen | £12,000–£25,000 | Knock through |
| Buy and install dishwasher | task | wish | Kitchen | – | – |
| Shower room / bathroom refit | project | improvement | Shower room | £5,000–£9,000 | – |
| Solid wall insulation, internal (decision group: wall insulation) | project | improvement | Whole house | £7,000–£12,000 | Survey import |
| Solid wall insulation, external (decision group: wall insulation) | project | improvement | Exterior and roof | £12,000–£25,000 | Survey import, Article 4 check |
| Windows: repair or replace | project | improvement | Whole house | – | Survey import, Article 4 check |
| Repaint throughout | project | improvement | Whole house | £3,000–£6,000 labour + 15–25% materials | Knock through, wall insulation, windows |
| New carpets (bedrooms, stairs) | project | improvement | Whole house | £2,000–£4,000 | Repaint throughout |
| Loft conversion (rear dormer with en-suite) | project | improvement | Loft | £35,000–£55,000 | Survey import |
| Lawful Development Certificate for garage use | task | enabling | Garage | confirm current fee | Article 4 check |
| CCTV drainage survey, garage to main sewer | task | enabling | Garage | – | – |
| Garage conversion shell (insulation, floor, electrics) | project | improvement | Garage | £15,000–£25,000 | Lawful Development Certificate |
| Garage wet room incl. drainage extension | project | improvement | Garage | £8,000–£15,000 | Garage shell, CCTV drainage survey |
| Garage sauna and dedicated circuit | project | wish | Garage | £4,000–£10,000 | Garage shell |
| Solar panels | project | improvement | Exterior and roof | – | Survey import |
| Front garden bike and bin storage (`needs_planning_check`: outbuildings forward of the principal elevation fall outside householder permitted development) | project | improvement | Front garden | – | Article 4 check |
| Rear garden landscaping | project | improvement | Rear garden | – | – |
| Buy a mirror | wish | wish | – | – | – |

Flag `needs_building_control` on: knock through, underfloor heating (if electrical work involved), all garage items, loft conversion, windows (replacement), kitchen and bathroom where electrics or drainage change.

## 13. Build milestones

1. **Core**: models, migrations, service layer with activity log, auth, rooms/phases/items CRUD, quick add, items list and detail. Runs locally.
2. **Quotes and people**: quotes, contractors, attachments with phone camera upload, notes.
3. **Planning**: dependencies with cycle check, next actions, decision groups, budget and savings views, timeline with warnings.
4. **MCP**: tools per §8, OAuth per §9, tested locally with MCP Inspector.
5. **Networking**: Tailscale, Funnel (or Cloudflare Tunnel), connector added in claude.ai, end-to-end test from a phone including a mock survey import.
6. **Operations**: backups with a tested restore, seed data, README.

Tests limited to: cycle detection, budget and next-action calculations, and that the public listener rejects unauthenticated MCP calls and serves no UI routes.

## 14. Out of scope

Native mobile apps, notifications or email, multi-property support, server-side PDF parsing, public (non-tailnet) web UI.

## 15. Resolve with the owner before starting

1. PC operating system, and whether it can stay on continuously (Windows needs Docker Desktop with WSL2).
2. Whether they own a domain (only relevant if choosing Cloudflare Tunnel over Funnel).
3. Backup destination (external drive, cloud storage account).
4. Usernames and display names for both accounts; whether both have Claude accounts.
5. Quote threshold (default £1,000) and monthly saving target.

## 16. References

- Anthropic, "Get started with custom connectors using remote MCP": support.claude.com/en/articles/11175166
- Anthropic, "Building custom connectors via remote MCP servers": support.claude.com/en/articles/11503834
- Model Context Protocol specification, including authorisation: modelcontextprotocol.io
- MCP Python SDK: github.com/modelcontextprotocol/python-sdk
- Tailscale documentation (install, Funnel): tailscale.com/kb
- Cloudflare Tunnel documentation: developers.cloudflare.com
- Planning Portal, householder permitted development: planningportal.co.uk
- SQLite online backup API: sqlite.org/backup.html
