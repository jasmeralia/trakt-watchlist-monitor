import sqlite3
from datetime import UTC, datetime

# pylint: disable=too-many-arguments,too-many-positional-arguments


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trakt_id     INTEGER NOT NULL,
            media_type   TEXT    NOT NULL,
            quality      TEXT    NOT NULL,
            price        REAL    NOT NULL,
            currency     TEXT    NOT NULL DEFAULT 'USD',
            observed_at  TEXT    NOT NULL,
            UNIQUE(trakt_id, media_type, quality) ON CONFLICT REPLACE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            trakt_id       INTEGER NOT NULL,
            media_type     TEXT    NOT NULL,
            quality        TEXT    NOT NULL,
            notified_at    TEXT    NOT NULL,
            price          REAL    NOT NULL,
            original_price REAL    NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_last_price(
    conn: sqlite3.Connection, trakt_id: int, media_type: str, quality: str
) -> float | None:
    row = conn.execute(
        """
        SELECT price
        FROM price_history
        WHERE trakt_id = ? AND media_type = ? AND quality = ?
        """,
        (trakt_id, media_type, quality),
    ).fetchone()
    if row is None:
        return None
    return float(row[0])


def upsert_price(
    conn: sqlite3.Connection,
    trakt_id: int,
    media_type: str,
    quality: str,
    price: float,
    currency: str = "USD",
) -> None:
    conn.execute(
        """
        INSERT INTO price_history (trakt_id, media_type, quality, price, currency, observed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (trakt_id, media_type, quality, price, currency, _utc_now()),
    )
    conn.commit()


def was_notified(
    conn: sqlite3.Connection,
    trakt_id: int,
    media_type: str,
    quality: str,
    price: float,
    last_price: float | None = None,
) -> bool:
    # last_price is the most recently stored price. If it exceeds the notified price,
    # the price went up since we last notified, resetting the "on sale" state.
    row = conn.execute(
        """
        SELECT 1
        FROM notification_log
        WHERE trakt_id = ?
            AND media_type = ?
            AND quality = ?
            AND price >= ?
            AND (? IS NULL OR ? <= price)
        LIMIT 1
        """,
        (trakt_id, media_type, quality, price, last_price, last_price),
    ).fetchone()
    return row is not None


def log_notification(
    conn: sqlite3.Connection,
    trakt_id: int,
    media_type: str,
    quality: str,
    price: float,
    original_price: float,
) -> None:
    conn.execute(
        """
        INSERT INTO notification_log (
            trakt_id, media_type, quality, notified_at, price, original_price
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (trakt_id, media_type, quality, _utc_now(), price, original_price),
    )
    conn.commit()


def reset_notification_state(conn: sqlite3.Connection) -> dict[str, int]:
    # Restore price_history from the most recent non-zero original_price in notification_log.
    # This lets the next check run re-detect the qualifying drop without corrupting history.
    conn.execute(
        """
        UPDATE price_history
        SET price = (
            SELECT original_price
            FROM notification_log
            WHERE notification_log.trakt_id = price_history.trakt_id
                AND notification_log.media_type = price_history.media_type
                AND notification_log.quality = price_history.quality
                AND original_price > 0.0
            ORDER BY notified_at DESC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM notification_log
            WHERE notification_log.trakt_id = price_history.trakt_id
                AND notification_log.media_type = price_history.media_type
                AND notification_log.quality = price_history.quality
                AND original_price > 0.0
        )
        """
    )
    prices_restored = conn.execute("SELECT changes()").fetchone()[0]

    # For items that were only ever first-observations-below-threshold (original_price=0.0),
    # delete the price record so next run treats them as fresh first observations.
    conn.execute(
        """
        DELETE FROM price_history
        WHERE EXISTS (
            SELECT 1 FROM notification_log
            WHERE notification_log.trakt_id = price_history.trakt_id
                AND notification_log.media_type = price_history.media_type
                AND notification_log.quality = price_history.quality
        )
        AND NOT EXISTS (
            SELECT 1 FROM notification_log
            WHERE notification_log.trakt_id = price_history.trakt_id
                AND notification_log.media_type = price_history.media_type
                AND notification_log.quality = price_history.quality
                AND original_price > 0.0
        )
        """
    )
    prices_cleared = conn.execute("SELECT changes()").fetchone()[0]

    conn.execute("DELETE FROM notification_log")
    notifications_cleared = conn.execute("SELECT changes()").fetchone()[0]

    conn.commit()
    return {
        "prices_restored": prices_restored,
        "prices_cleared": prices_cleared,
        "notifications_cleared": notifications_cleared,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
