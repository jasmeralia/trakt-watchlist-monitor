# Adversarial Review

Reviewed on 2026-05-15. All five application modules plus the test suite.

---

## Critical — silent wrong results or data loss

**`trakt_watchlist_monitor/pricing.py` ~line 67 — SMTP failure permanently silences future alerts**

When `notify.send_alert()` raises `SMTPException`, the `except` branch logs and
continues — but `db.upsert_price()` on line 78 still runs, writing the new lower
price into `price_history`. On the next poll, that lower price is no longer less
than the stored price, so the alert condition is never re-evaluated. The user never
receives the notification, and no future drop can recover it until the price goes
even lower.

`tests/test_pricing.py` (notify-failure test) asserts this as correct behavior — the
bug is encoded into the expected output.

**`trakt_watchlist_monitor/db.py` line 84 — `was_notified()` semantics suppress deeper discounts**

The predicate `price >= ?` (comparing the current price to the stored notification
price) returns `True` when the current price is at or below the previously notified
price. This means: after a $10 alert fires, a subsequent drop to $7.99 is silently
suppressed forever because `10.00 >= 7.99`.

`tests/test_db.py:64` asserts `was_notified(... 7.99) is True` after a $10.00
notification — locking in this behavior as intentional. The design doc says "no
notification has been sent at this price" but neither `<=` nor `>=` matches that
literally.

---

## High — crashes or missed alerts on real inputs

**`trakt_watchlist_monitor/trakt.py` — Refresh token never persisted to disk**

`refresh_token()` mutates the in-memory `settings` object but never writes the new
tokens back to `.env` or any persistent store. Trakt rotates refresh tokens on use.
After a container restart (or any process exit), the old refresh token is loaded from
`.env`; the next refresh attempt will fail, and the service will stop fetching
watchlists permanently.

`tests/test_trakt.py` verifies only the in-memory mutation.

**`trakt_watchlist_monitor/main.py` — Top-level exception kills the service permanently**

If `check_prices()` raises (SQLite cannot open `/data/prices.db`, Trakt returns 5xx
before the item loop starts, etc.), the exception propagates out of the `while True`
loop and the service exits. The polling loop has no outer `try/except` to log and
sleep through transient failures.

No test covers this path.

**`trakt_watchlist_monitor/trakt.py` `_normalize_watchlist_item()` — Season entries silently dropped**

The function branches on `item["type"] == "movie"` / `"show"`, but the Trakt API
also returns `type == "season"` for season-level watchlist entries. The design doc
(§ Overview) mentions seasons as a supported entity. Any season entry is silently
ignored — no monitoring, no log message.

No test covers a season-typed API response.

---

## Medium — behavioral gaps, untested edge cases

**`trakt_watchlist_monitor/pricing.py` `select_best_quality()` — cheapest same-quality offer not selected**

JustWatch can return two HD buy offers for the same item at different prices (e.g.,
$12.99 and $7.99). `select_best_quality()` sorts only by quality tier and takes the
first match. The cheaper same-quality price may be silently ignored, causing
threshold checks to fail against an inflated baseline.

No test covers duplicate quality entries.

**`trakt_watchlist_monitor/db.py` — Currency not part of the price lookup key**

`price_history` stores `currency` but `get_last_price()` returns only the numeric
value with no currency column. If a title's JustWatch offer switches currency (e.g.,
stored as USD $9.99, later returned as GBP £7.99 due to a locale mismatch), the
comparison treats £7.99 as a discount off $9.99 and fires a false alert.

No test covers cross-currency comparisons.

**`trakt_watchlist_monitor/justwatch.py` `_parse_price()` — Locale-formatted prices yield bogus numbers**

The regex `r"\d+(?:\.\d+)?"` extracts the first numeric sequence. For
`"€9,99"` (comma-decimal locale) it returns `9`; for `"1,299.00"` (thousands
separator) it returns `1`. Either case produces a phantom price drop that will fire
a false alert.

No test covers comma-decimal or thousands-separator formats.

**`trakt_watchlist_monitor/justwatch.py` — GraphQL `"errors"` array silently treated as empty result**

A partial JustWatch response (e.g., schema error, rate limit) may include an
`"errors"` key alongside `"data": null`. The code checks only for `data.node`
being a dict; a missing or null node returns `[]`, indistinguishable from "no
Amazon offer found." The upstream error is never logged.

No test covers a response with an `"errors"` key.

**`trakt_watchlist_monitor/notify.py` — STARTTLS hardcoded; port 465 / implicit TLS fails silently**

`smtplib.SMTP()` followed by `starttls()` is only correct for STARTTLS on port 587.
Port 465 (implicit TLS) requires `smtplib.SMTP_SSL()`. A user who sets
`SMTP_PORT=465` will get a TLS handshake error (or silently send credentials in the
clear if the server downgrades). The design doc does not restrict SMTP to STARTTLS
only.

No test covers this path.

**`trakt_watchlist_monitor/notify.py` — No SMTP socket timeout**

`smtplib.SMTP()` is called without a `timeout` argument. A server that accepts
the TCP connection but stalls will block `send_alert()` indefinitely, freezing
the entire monitoring loop for that poll cycle.

**`trakt_watchlist_monitor/pricing.py` `meets_discount_threshold()` — ZeroDivisionError on $0.00 baseline**

If JustWatch returns a buy price of `0.0` (e.g., a temporary free promotion) and
that gets stored as `last_price`, the next run divides by zero inside the discount
percentage formula. This will crash `check_prices()` for that item.

No test covers a zero baseline price.

---

## Low / Design Questions

**`price_history` table name implies history but stores only one row per key**

The `UNIQUE(trakt_id, media_type, quality) ON CONFLICT REPLACE` constraint means
the table is effectively a "current price" store. The name `price_history` is
misleading, and any retrospective trend analysis would be impossible. Confirmed by
`tests/test_db.py:41`.

**`collection_ids` keyed only on `trakt_id`, not `(media_type, trakt_id)`**

Trakt IDs are scoped per media type (movies and shows share the same ID space on
Trakt's side, but this is worth verifying). If a movie and a show happen to share a
numeric ID, the collected-movie filter could incorrectly exclude a watchlisted show
from monitoring.

---

## Test Suite Assessment

The suite covers the happy path and key orchestration branches well: first
observation, threshold alert, below-threshold suppression, per-item failure
continuation, Trakt 401 refresh, basic JustWatch parsing, and DB replacement.

**The critical flaw** is that two real bugs are encoded as expected behavior:

1. `tests/test_pricing.py` (notify-failure test) asserts that a price drop is stored
   even when the email fails — permanently locking out future alerts for that item.
2. `tests/test_db.py:64` asserts that `was_notified()` suppresses a deeper future
   discount — encoding the threshold inversion as correct.

**Coverage gaps with no tests at all:**
- Season-type watchlist entries
- JustWatch `"errors"` response payload
- Multiple same-quality offers for one item
- Cross-currency price comparisons
- Comma-decimal / thousands-separator price strings
- Trakt startup failure or SQLite open failure
- Main-loop crash resilience
- SMTP implicit TLS (port 465)
- SMTP socket timeout
- Zero baseline price (ZeroDivisionError)
