# Renovation Tracker

A private web app for tracking a house renovation: everything from "buy a mirror" to
"loft conversion". Two people can use it from their phones over Tailscale, and Claude
can read and edit it too (for example, importing a building survey PDF) through a
public, OAuth-protected connector. It runs on your own PC — nothing is stored in
someone else's cloud.

This guide assumes no programming experience. Follow it top to bottom.

## What you'll end up with

- A private web app, reachable from your phone/laptop anywhere via Tailscale.
- A separate, locked-down connection Claude uses to read/edit your data.
- Nightly backups, kept 14 days + 12 months, with a restore you'll test once.

## 1. Install Docker

- **Windows**: install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
  It needs WSL2 — Docker Desktop's installer offers to set this up; accept it, then
  restart your PC when asked.
- **Mac**: install Docker Desktop as above.
- **Linux**: install `docker` and the `docker compose` plugin from your distro's
  package manager, or follow [docs.docker.com/engine/install](https://docs.docker.com/engine/install/).

Confirm it worked by opening a terminal (PowerShell on Windows, Terminal on Mac/Linux)
and running:

```
docker --version
docker compose version
```

Both should print a version number.

## 2. Get the code

```
git clone <this repository's URL>
cd Home-Renovation-Public-
```

## 3. Configure

Copy the example settings file:

```
cp .env.example .env
```

Open `.env` in a text editor and fill in:

- **SECRET_KEY** — generate one and paste it in:
  ```
  docker compose run --rm app python -c "import secrets; print(secrets.token_hex(32))"
  ```
- **PUBLIC_BASE_URL** — leave the placeholder for now; you'll set this in step 6
  once Tailscale gives you a real address.
- **TAILSCALE_IP** — leave the placeholder for now; you'll fill this in during step 5.
- Leave **SECURE_COOKIES=false** until step 6, then set it to `true`.
- **RCLONE_REMOTE** — optional, see step 8.

## 4. Create the database and your accounts

```
docker compose run --rm app python -m app.cli init
docker compose run --rm app python -m app.cli seed
```

`seed` loads a starting list of rooms, budget phases and example renovation items
(kitchen, bathroom, loft, garage, etc.) so the app isn't empty on first use — edit or
delete anything you don't need. **Assumption made without asking you first:** the
loft conversion, all garage projects, solar panels, external wall insulation and the
kitchen refit were put in the "Later" phase rather than "Phase 1", since Phase 1's
£10–20k budget can't cover them; everything else was put in Phase 1. Move items
between phases any time from the Budget page.

Now create an account for each of you (run once per person):

```
docker compose run --rm app python -m app.cli adduser niall "Niall"
docker compose run --rm app python -m app.cli adduser <username2> "<Display Name>"
```

You'll be asked to type a password twice (it won't show on screen).

## 5. Start the app and install Tailscale

Start the app:

```
docker compose up -d
```

Install Tailscale on this PC and on every phone/laptop that should reach the app:
[tailscale.com/kb](https://tailscale.com/kb) has installers for every platform. Sign
in to the same Tailscale account (a free personal account is fine) on all of them.

On this PC, find its Tailscale address:

```
tailscale ip -4
```

Put that address into `.env` as `TAILSCALE_IP`, then restart the app so it picks it up:

```
docker compose up -d
```

From another device on your tailnet, open `http://<that address>:8000` in a browser —
you should see the login page.

## 6. Give Claude access (public connector)

Claude runs in Anthropic's cloud, so it can't reach your tailnet directly. Tailscale
Funnel exposes just the app's public port (8001) to the internet over HTTPS, without
opening any ports on your router.

```
tailscale funnel --bg 8001
```

Flags and defaults change between Tailscale versions, so check the current syntax
against [tailscale.com/kb/1223/funnel](https://tailscale.com/kb/1223/funnel) before
running it. Funnel prints an `https://....ts.net` address — copy it.

Put that address into `.env` as `PUBLIC_BASE_URL` (include `https://`, no trailing
slash), set `SECURE_COOKIES=true`, and restart:

```
docker compose up -d
```

**Alternative:** if you already own a domain on Cloudflare, you can use Cloudflare
Tunnel instead of Funnel, pointed at the same port 8001. See
[developers.cloudflare.com](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

Now add the connector in Claude:

1. Go to claude.ai → Customize → Connectors (older layouts: Settings → Connectors) → Add custom connector.
2. Enter `<your PUBLIC_BASE_URL>/mcp` as the URL.
3. Sign in with your app account when prompted.

Each householder with a Claude account repeats step 3 with their own login. Full
walkthrough: [support.claude.com/en/articles/11175166](https://support.claude.com/en/articles/11175166).
Only port 8001 is ever exposed publicly — the web UI (port 8000) stays tailnet-only.

## 7. Keep the PC awake

The app only works while the PC is on and not asleep.

- **Windows**: Settings → System → Power & battery → Screen and sleep → set
  "When plugged in, put my device to sleep" to Never. Also disable sleep on lid-close
  in Control Panel → Power Options if it's a laptop.
- **Mac**: System Settings → Energy Saver / Lock Screen → "Prevent automatic sleeping
  when the display is off" (on plugged-in power).
- **Linux**: disable suspend in your desktop's power settings, or
  `systemctl mask sleep.target suspend.target`.

Docker's `restart: unless-stopped` means the app container restarts automatically
after a reboot or crash, once Docker itself is running (Docker Desktop can be set to
start on login).

## 8. Backups

A second container runs a backup once every 24 hours automatically (see the `backup`
service in `docker-compose.yml`), writing to `./data/backups` on this PC. Each backup
is a `renovation-YYYY-MM-DD.tar.gz` containing the database and your uploaded
photos/receipts, made with SQLite's online backup API (never a raw file copy — see
[sqlite.org/backup.html](https://www.sqlite.org/backup.html)), so it's safe to take
while the app is running. Old backups are pruned automatically, keeping the last 14
daily copies plus one per month for 12 months.

**Off-machine copy (recommended):** if a burglary, fire or disk failure would take out
your only copy, set `RCLONE_REMOTE` in `.env` to an [rclone](https://rclone.org)
remote name (e.g. a OneDrive or Google Drive folder you've configured with
`rclone config`) and each backup will also be copied there.

**Manual backup any time:**

```
docker compose run --rm app python -m app.cli backup
```

**Restore** (stop the app first, so nothing writes to the database mid-restore):

```
docker compose stop app
docker compose run --rm app python -m app.cli restore /data/backups/renovation-2026-01-01.tar.gz
docker compose start app
```

**Test your restore now**, before you rely on this system: run a manual backup, note
the item count on the dashboard, restore that same backup into a spare folder to
confirm it works, then confirm your real data is untouched.

**JSON export:** a full export of your data (excluding passwords and login tokens) is
available any time:

```
docker compose run --rm app python -m app.cli export-json /data/export.json
```

## 9. Recovery

**Container won't start:** check logs with `docker compose logs app`. Common causes:
a typo in `.env`, or the `data` folder having wrong permissions — try
`docker compose down && docker compose up -d`.

**Forgot a password:**

```
docker compose run --rm app python -m app.cli passwd <username>
```

**Lost the whole PC / need to move to a new one:** install Docker and Tailscale on
the new machine, copy your `.env` and your most recent `./data/backups/*.tar.gz`
across, run `docker compose run --rm app python -m app.cli init`, then `restore` that
archive, then `docker compose up -d`.

## Day-to-day use

- Web UI: `http://<TAILSCALE_IP>:8000` from any device on your tailnet.
- Add things quickly with the single text box on every page — press Enter and fill
  in details later.
- Claude: once connected (step 6), just ask it things like "add a task to get gutter
  quotes" or paste in a survey PDF and ask it to log the defects.

## Reference documentation

- Claude custom connectors: [support.claude.com/en/articles/11175166](https://support.claude.com/en/articles/11175166)
- Tailscale: [tailscale.com/kb](https://tailscale.com/kb)
- SQLite backup API: [sqlite.org/backup.html](https://www.sqlite.org/backup.html)
