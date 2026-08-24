# Design: trakt_watchlist_monitor

## Purpose

A Python service that runs continuously or as a single cron-invoked cycle and:
1. Fetches a user's Trakt watchlist (movies and TV show seasons)
2. Excludes items already in their Trakt collection
3. Looks up Amazon Prime Video buy prices for those items via JustWatch
4. Compares current prices against historical prices stored in SQLite
5. Sends an SMTP email alert when a price drops by at least `DISCOUNT_THRESHOLD_PERCENT`

## High-Level Flow

```
[Trakt API] ──► watchlist - collection = effective watchlist
                          │
                          ▼
               [JustWatch API] ──► Amazon Prime buy prices per item
                          │
                          ▼
               [SQLite DB] ──► compare against last-seen price
                          │
              (if drop ≥ threshold)
                          │
                          ▼
               [SMTP / notify.py] ──► email alert
```

## Module Responsibilities

| Module | Purpose |
|---|---|
| `config.py` | Pydantic `Settings`; reads `.env`; single source of truth for all configuration |
| `trakt.py` | Trakt API client: OAuth token refresh, watchlist fetch, collection fetch |
| `justwatch.py` | JustWatch price lookup: TMDB ID → JustWatch ID → Amazon Prime buy prices |
| `pricing.py` | Quality-tier selection (UHD > HD > SD), discount threshold evaluation, orchestration |
| `db.py` | SQLite persistence: price history schema, read/write helpers, notification log |
| `notify.py` | SMTP email alerts via `smtplib` (Gmail App Password supported) |
| `main.py` | Entry point: runs `check_prices()` continuously, or once with `--once`/`RUN_ONCE` |

## Configuration Schema

All configuration is read from `.env` (bind-mounted at `/app/.env` in Docker) via pydantic-settings.
See `.env.example` for a template.

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `TRAKT_CLIENT_ID` | str | — | Yes | Trakt application client ID |
| `TRAKT_CLIENT_SECRET` | str | — | Yes | Trakt application client secret |
| `TRAKT_ACCESS_TOKEN` | str | — | Yes | OAuth access token |
| `TRAKT_REFRESH_TOKEN` | str | — | Yes | OAuth refresh token |
| `TRAKT_USERNAME` | str | — | Yes | Trakt username |
| `SMTP_HOST` | str | — | Yes | SMTP server hostname |
| `SMTP_PORT` | int | 587 | No | SMTP server port |
| `SMTP_USERNAME` | str | — | Yes | SMTP login username |
| `SMTP_PASSWORD` | str | — | Yes | SMTP login password (App Password for Gmail) |
| `SMTP_FROM` | str | — | Yes | Sender email address |
| `SMTP_TO` | str | — | Yes | Recipient email address |
| `DISCOUNT_THRESHOLD_PERCENT` | float | 20.0 | No | Minimum % price drop to trigger notification |
| `CHECK_INTERVAL_HOURS` | float | 6.0 | No | Hours between price checks |
| `API_REQUEST_INTERVAL_SECONDS` | float | 1.5 | No | Minimum delay between external API requests |
| `DB_PATH` | str | /data/prices.db | No | SQLite database file path |
| `LOG_LEVEL` | str | INFO | No | Logging verbosity: CRITICAL, ERROR, WARNING, INFO, DEBUG, or NOTSET |
| `RUN_ONCE` | bool | false | No | Run a single check cycle and exit instead of looping (also settable via `--once`) |

## SQLite Schema

### `price_history`

Stores the most recent price observed for each item/quality combination.

```sql
CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trakt_id     INTEGER NOT NULL,
    media_type   TEXT    NOT NULL,  -- 'movie' or 'show'
    quality      TEXT    NOT NULL,  -- 'UHD', 'HD', or 'SD'
    price        REAL    NOT NULL,
    currency     TEXT    NOT NULL DEFAULT 'USD',
    observed_at  TEXT    NOT NULL,  -- ISO 8601 UTC timestamp
    UNIQUE(trakt_id, media_type, quality) ON CONFLICT REPLACE
);
```

### `notification_log`

Tracks which price points have already triggered notifications to prevent duplicate alerts.

```sql
CREATE TABLE IF NOT EXISTS notification_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trakt_id       INTEGER NOT NULL,
    media_type     TEXT    NOT NULL,
    quality        TEXT    NOT NULL,
    notified_at    TEXT    NOT NULL,  -- ISO 8601 UTC timestamp
    price          REAL    NOT NULL,
    original_price REAL    NOT NULL
);
```

## Docker Deployment

The recommended deployment method is Docker with two bind mounts:

```bash
docker run -d \
  --name trakt-watchlist-monitor \
  --restart unless-stopped \
  -v "$(pwd)/.env:/app/.env:ro" \
  -v trakt_data:/data \
  ghcr.io/jasmeralia/trakt-watchlist-monitor:latest
```

- `.env` is mounted read-only at `/app/.env` — the app never writes to it
- `/data` is a named volume where SQLite persists price history between container restarts
- The container runs as a non-root user (`appuser`, uid 1001)

## Native Cron Deployment

For schedulers such as a native TrueNAS cron job, sync the uv environment and invoke one cycle:

```bash
uv sync --locked
uv run python -m trakt_watchlist_monitor --once
```

## Quality-Tier Priority

When multiple buy-price tiers are available for the same item, only the highest available quality
is tracked and compared:

```
UHD (priority 3) > HD (priority 2) > SD (priority 1)
```

This means if an item is available in UHD and HD, only the UHD price is tracked.

## Discount Threshold Formula

```
price_drop_percent = (original_price - current_price) / original_price × 100
```

A notification is sent when **all** of the following are true:

1. A prior price exists in `price_history` for this `(trakt_id, media_type, quality)` tuple
2. The current price is strictly lower than the stored price
3. `price_drop_percent >= DISCOUNT_THRESHOLD_PERCENT`
4. No prior notification has been sent at or below this price (checked via `notification_log`)

After notifying, the new (lower) price is written to `price_history`.

## Development Workflow

```bash
make venv       # sync the uv-managed .venv and install all dependencies
make lintfix   # auto-fix ruff style/import issues
make lint       # ruff + mypy + pylint + shellcheck + hadolint
make test       # pytest
make clean      # remove .venv and all caches
```

All code changes must pass `make lintfix && make lint && make test` before commit.

## Release Process

Merges to `master` trigger the release workflow automatically:

1. CI (lint + test) runs as a required check on the PR
2. After merge, the release workflow computes the next semver patch tag (`v0.1.2` → `v0.1.3`)
3. The tag is pushed to GitHub
4. A Docker image is built and pushed to GHCR tagged as `0.1.3` and `latest`

Git tags use a `v` prefix (`v1.2.3`); Docker image tags do not (`1.2.3`).
