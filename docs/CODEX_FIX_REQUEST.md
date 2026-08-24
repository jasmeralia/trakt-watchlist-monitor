# Codex Fix Request — Adversarial Review

## Source of Truth

- `docs/DESIGN.md` — architecture and module contracts
- `docs/ADVERSARIAL_REVIEW.md` — full adversarial review findings

## Scope

Fixes for findings that have a clear correct answer and do not require config schema
changes or DB migrations. Items that require design decisions (token persistence,
currency key, SMTP_SSL, season support) are explicitly out of scope here.

## Checklist

- [x] **main-loop-resilience** `trakt_watchlist_monitor/main.py`: Wrap the body of the `while True` loop in a
  `try/except Exception` that logs the error to stderr and sleeps before retrying, so a
  transient Trakt failure or DB open error does not kill the service permanently.

- [x] **smtp-timeout** `trakt_watchlist_monitor/notify.py`: Pass a `timeout` argument (e.g. `30`) to
  `smtplib.SMTP()` so a stalled server cannot block the monitoring loop indefinitely.

- [x] **graphql-errors** `trakt_watchlist_monitor/justwatch.py`: After `response.raise_for_status()`, inspect
  the parsed JSON for a top-level `"errors"` key. If present, log the first error message
  to stderr and return `[]` rather than silently falling through as if no offers were found.

- [x] **price-parse-locale** `trakt_watchlist_monitor/justwatch.py`: Fix `_parse_price()` so that
  comma-decimal strings like `"€9,99"` and thousands-separator strings like `"1,299.00"`
  do not produce bogus numbers. The correct approach: strip all non-digit, non-period, and
  non-comma characters, replace any comma that acts as a decimal separator (rightmost
  comma followed by exactly two digits at end of string) with a period, then remove
  remaining commas, and parse the result. Add tests for both formats.

- [x] **zero-division-guard** `trakt_watchlist_monitor/pricing.py`: Guard against `last_price == 0.0` in the
  discount-percentage calculation inside `check_prices()`. If `last_price` is zero, skip
  the notification logic (a zero baseline is not a meaningful reference price) and still
  call `upsert_price`.

- [x] **upsert-after-alert** `trakt_watchlist_monitor/pricing.py` + `tests/test_pricing.py`: The critical bug:
  when the threshold is met and alert sending fails, `upsert_price` still runs, permanently
  preventing future re-alerts. Fix: only call `upsert_price` when the alert was either
  (a) sent successfully, or (b) not required (below threshold, first observation, price
  increase). When an alert was required but the send failed, skip `upsert_price` so the
  next run retries. Update the notify-failure test to assert that `upsert_price` is NOT
  called when send fails.

- [x] **season-log** `trakt_watchlist_monitor/trakt.py`: In `_normalize_watchlist_item()`, add an explicit
  `elif item["type"] not in {"movie", "show"}` branch that logs the unsupported type to
  stderr and returns `None`, rather than falling through silently.

- [x] **select-cheapest-quality** `trakt_watchlist_monitor/pricing.py` `select_best_quality()`: When multiple
  offers share the same quality tier, select the one with the lowest price rather than the
  first encountered. Update or add a test for this case.

- [x] **currency-guard** `trakt_watchlist_monitor/pricing.py`: After extracting the currency from the best-price
  offer inside `check_prices()`, check whether it equals `"USD"`. If not, log a warning to
  stderr (`f"Unexpected currency {currency!r} for trakt_id {trakt_id}; skipping"`) and
  `continue` to the next item. This prevents a future currency switch from causing a
  mixed-currency price comparison. The expected currency is `"USD"` (hardcoded constant in
  pricing.py — no new config field). Add a test asserting that a non-USD offer skips
  `upsert_price` and logs to stderr.

- [x] **token-persistence** `trakt_watchlist_monitor/trakt.py` + `trakt_watchlist_monitor/config.py`: After a successful Trakt token
  refresh in `refresh_token()`, write the new access and refresh tokens to a file at
  `os.path.join(os.path.dirname(settings.db_path), "tokens.env")` (e.g. `/data/tokens.env`
  alongside the DB). Write only two lines: `TRAKT_ACCESS_TOKEN=<value>` and
  `TRAKT_REFRESH_TOKEN=<value>`. Wrap the write in a non-fatal `try/except OSError` that logs
  to stderr if it fails. In `trakt_watchlist_monitor/config.py`, extend `env_file` from `".env"` to the tuple
  `(".env", "/data/tokens.env")` so that persisted tokens are loaded on the next restart
  (pydantic-settings silently skips missing files). Do not add any new Settings fields. Add a
  test to `tests/test_trakt.py` verifying that after a 401 refresh, the token file is written
  with the new values.

- [x] **smtp-ssl** `trakt_watchlist_monitor/notify.py`: If `settings.smtp_port == 465`, use
  `smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)` and skip the
  `starttls()` call. For all other ports keep the existing `smtplib.SMTP(..., timeout=30)`
  + `starttls()` path. No new config fields. Update or add tests to cover both branches.

## Constraints

- Do not add new config fields or modify pydantic Settings (beyond the env_file tuple change
  in config.py for token-persistence).
- Do not change the DB schema or add migrations.
- Do not implement season lookup via JustWatch (out of scope).
- All changes must pass `make lintfix && make lint && make test`.
- Follow existing project style (typed, ruff-formatted, pylint-clean).

## Success Criteria

- All checklist items marked complete.
- `make test` passes with no regressions.
- `make lint` passes (ruff, mypy, pylint).

## Completed by Codex

- Completed `main-loop-resilience` on 2026-05-15: `main()` now catches and logs unexpected
  loop failures with traceback details, then waits the configured interval before retrying.
- Completed `smtp-timeout` on 2026-05-15: SMTP alert delivery now uses a 30-second
  connection timeout.
- Completed `zero-division-guard` on 2026-05-15: zero last-price baselines now bypass
  discount notification checks while still recording the observed price.
- Completed `season-log` on 2026-05-15: unsupported Trakt watchlist item types now log to
  stderr before being ignored.
- Completed `select-cheapest-quality` on 2026-05-15: same-quality offers now choose the
  lowest price within the highest available quality tier.
- Completed `currency-guard` on 2026-05-15: non-USD best-price offers now log a warning and
  skip price persistence for that item.
- Completed `smtp-ssl` on 2026-05-15: port 465 now uses implicit TLS via `SMTP_SSL`, while
  other SMTP ports continue to use STARTTLS.
- Completed `token-persistence` on 2026-05-16: refreshed Trakt tokens now persist to
  `tokens.env` beside the SQLite database and are loaded from `/data/tokens.env` on restart.
