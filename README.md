# Trakt Watchlist Monitor

[![CI](https://github.com/jasmeralia/trakt-watchlist-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/jasmeralia/trakt-watchlist-monitor/actions/workflows/ci.yml)

A self-hosted service that monitors your [Trakt](https://trakt.tv) watchlist for Amazon Prime Video
buy-price discounts and emails you when a watched item drops below your configured threshold.

## What it does

1. Fetches your Trakt watchlist (movies and TV seasons)
2. Excludes anything already in your Trakt collection
3. Looks up Amazon Prime Video buy prices via JustWatch
4. Tracks prices in a local SQLite database
5. Sends an email notification when a price drops by at least `DISCOUNT_THRESHOLD_PERCENT`

Only the highest available quality tier is tracked per item (UHD preferred over HD, HD over SD).

## Prerequisites

- A [Trakt](https://trakt.tv) account with a watchlist and collection
- Docker (for the recommended deployment method), or Python 3.12+ with
  [uv](https://docs.astral.sh/uv/) installed
- An email account with SMTP access (Gmail is supported)

## Obtaining API Keys

### Trakt

1. Log in to [trakt.tv](https://trakt.tv) and go to **Settings → Your API Apps**
   (or navigate directly to <https://trakt.tv/oauth/applications>)
2. Click **New Application**
3. Fill in a name (e.g., `watchlist-monitor`), set **Redirect URI** to `urn:ietf:wg:oauth:2.0:oob`,
   and click **Save App**
4. Note your **Client ID** and **Client Secret** — these go into `.env` as
   `TRAKT_CLIENT_ID` and `TRAKT_CLIENT_SECRET`
5. **Obtain OAuth tokens** using the device-code flow:

   ```bash
   # Step 1 — request a device code
   curl -s -X POST https://api.trakt.tv/oauth/device/code \
     -H "Content-Type: application/json" \
     -d '{"client_id": "YOUR_CLIENT_ID"}' | python3 -m json.tool
   ```

   Open the `verification_url` shown in the response and enter the `user_code`.

   ```bash
   # Step 2 — exchange the device code for tokens (run after completing Step 1 in browser)
   curl -s -X POST https://api.trakt.tv/oauth/device/token \
     -H "Content-Type: application/json" \
     -d '{
       "code": "YOUR_DEVICE_CODE",
       "client_id": "YOUR_CLIENT_ID",
       "client_secret": "YOUR_CLIENT_SECRET"
     }' | python3 -m json.tool
   ```

   The response contains `access_token` and `refresh_token` — save both to `.env` as
   `TRAKT_ACCESS_TOKEN` and `TRAKT_REFRESH_TOKEN`.

### SMTP (Email Notifications)

#### Gmail

1. Enable **2-Step Verification** on your Google account
   (<https://myaccount.google.com/security>)
2. Go to **Security → App Passwords**
   (<https://myaccount.google.com/apppasswords>)
3. Click **Create**, choose a name, and copy the 16-character password shown
4. Set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, and use the App Password as `SMTP_PASSWORD`

#### Other providers

Use your provider's SMTP hostname and credentials directly. Most support STARTTLS on port 587.

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
$EDITOR .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `TRAKT_CLIENT_ID` | Yes | — | Trakt application client ID |
| `TRAKT_CLIENT_SECRET` | Yes | — | Trakt application client secret |
| `TRAKT_ACCESS_TOKEN` | Yes | — | OAuth access token |
| `TRAKT_REFRESH_TOKEN` | Yes | — | OAuth refresh token |
| `TRAKT_USERNAME` | Yes | — | Your Trakt username |
| `SMTP_HOST` | Yes | — | SMTP server hostname |
| `SMTP_PORT` | No | `587` | SMTP server port |
| `SMTP_USERNAME` | Yes | — | SMTP login username |
| `SMTP_PASSWORD` | Yes | — | SMTP login password (App Password for Gmail) |
| `SMTP_FROM` | Yes | — | Sender email address |
| `SMTP_TO` | Yes | — | Recipient email address |
| `DISCOUNT_THRESHOLD_PERCENT` | No | `20.0` | Minimum % price drop to trigger a notification |
| `CHECK_INTERVAL_HOURS` | No | `6.0` | Hours between price checks |
| `API_REQUEST_INTERVAL_SECONDS` | No | `1.5` | Minimum delay between external API requests |
| `DB_PATH` | No | `/data/prices.db` | SQLite database path (inside container) |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`, or `NOTSET` |
| `RUN_ONCE` | No | `false` | Run a single check cycle and exit instead of looping (also settable via `--once`) |

## Running with Docker

```bash
docker run -d \
  --name trakt-watchlist-monitor \
  --restart unless-stopped \
  -v "$(pwd)/.env:/app/.env:ro" \
  -v trakt_data:/data \
  ghcr.io/jasmeralia/trakt-watchlist-monitor:latest
```

- `.env` is mounted read-only — the container never modifies it
- `trakt_data` is a named Docker volume where price history persists between restarts
- View logs: `docker logs trakt-watchlist-monitor`

To pin a specific release:

```bash
docker pull ghcr.io/jasmeralia/trakt-watchlist-monitor:1.2.3
```

## Running Locally

```bash
cp .env.example .env
$EDITOR .env
make venv
uv run python -m trakt_watchlist_monitor
```

## Scripts

`scripts/add_trakt_collection.py` manually adds or re-tags a single movie or episode in your
Trakt collection with full metadata (media type, resolution, audio, audio channels, HDR, 3D) —
useful when Sonarr (or another tool) fails to auto-add an item with the correct tags. It reads
Trakt credentials from the same `.env` used by the rest of this project.

```bash
make venv

# preview only, nothing sent
uv run python scripts/add_trakt_collection.py \
  --url "https://app.trakt.tv/movies/nobody-2021" \
  --resolution uhd_4k --audio dolby_atmos --audio-channels 7.1 --hdr dolby_vision

# check current collection metadata, no changes
uv run python scripts/add_trakt_collection.py --url "..." --check-only

# actually write it
uv run python scripts/add_trakt_collection.py --url "..." --resolution uhd_4k --commit
```

Run `uv run python scripts/add_trakt_collection.py --help` for the full option list.

## Development

```bash
make venv       # sync the uv-managed .venv, including development dependencies
make lintfix   # auto-fix formatting and import order with ruff
make lint       # ruff + mypy + pylint + shellcheck + hadolint
make test       # run pytest and write coverage.xml for Codecov
make clean      # remove .venv and all caches
```

All changes must pass `make lintfix && make lint && make test` before committing.
See [AGENTS.md](AGENTS.md) for development rules and [docs/DESIGN.md](docs/DESIGN.md) for
the architecture reference.

## CI/CD

- **Pull requests**: lint and tests run automatically as a required status check
- **Merges to master**: a new semver patch tag is created and a Docker image is built and
  pushed to GHCR as both `x.y.z` and `latest`
- **Dependabot**: weekly updates for Python packages, GitHub Actions, and the base Docker image
