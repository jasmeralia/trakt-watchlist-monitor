import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

# pylint: disable=too-many-arguments,too-many-positional-arguments

SCHEMA_VERSION = 1


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_items (
            trakt_id       INTEGER NOT NULL,
            media_type     TEXT    NOT NULL,
            title          TEXT    NOT NULL,
            year           INTEGER,
            trakt_slug     TEXT,
            tmdb_id        INTEGER,
            imdb_id        TEXT,
            listed_at      TEXT,
            in_watchlist   INTEGER NOT NULL DEFAULT 1,
            in_collection  INTEGER NOT NULL DEFAULT 0,
            justwatch_url  TEXT,
            image_url      TEXT,
            first_seen_at  TEXT    NOT NULL,
            last_seen_at   TEXT    NOT NULL,
            PRIMARY KEY (trakt_id, media_type)
        )
        """
    )
    current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current_version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def sync_watchlist_items(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> None:
    """Atomically upsert a complete watchlist snapshot and mark removed items absent."""
    now = _utc_now()
    snapshot = list(items)
    with conn:
        for item in snapshot:
            conn.execute(
                """
                INSERT INTO watchlist_items (
                    trakt_id, media_type, title, year, trakt_slug, tmdb_id, imdb_id,
                    listed_at, in_watchlist, in_collection, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(trakt_id, media_type) DO UPDATE SET
                    title = excluded.title,
                    year = excluded.year,
                    trakt_slug = excluded.trakt_slug,
                    tmdb_id = excluded.tmdb_id,
                    imdb_id = excluded.imdb_id,
                    listed_at = excluded.listed_at,
                    in_watchlist = 1,
                    in_collection = excluded.in_collection,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item["trakt_id"],
                    item["media_type"],
                    item["title"],
                    item.get("year"),
                    item.get("trakt_slug"),
                    item.get("tmdb_id"),
                    item.get("imdb_id"),
                    item.get("listed_at"),
                    int(bool(item.get("in_collection", False))),
                    now,
                    now,
                ),
            )

        conn.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS current_watchlist_keys (
                trakt_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                PRIMARY KEY (trakt_id, media_type)
            )
            """
        )
        conn.execute("DELETE FROM current_watchlist_keys")
        conn.executemany(
            "INSERT OR IGNORE INTO current_watchlist_keys (trakt_id, media_type) VALUES (?, ?)",
            [(item["trakt_id"], item["media_type"]) for item in snapshot],
        )
        if snapshot:
            conn.execute(
                """
                UPDATE watchlist_items SET in_watchlist = 0
                WHERE NOT EXISTS (
                    SELECT 1 FROM current_watchlist_keys AS current
                    WHERE current.trakt_id = watchlist_items.trakt_id
                        AND current.media_type = watchlist_items.media_type
                )
                """
            )
        else:
            conn.execute("UPDATE watchlist_items SET in_watchlist = 0")


def update_watchlist_item_metadata(
    conn: sqlite3.Connection,
    trakt_id: int,
    media_type: str,
    justwatch_url: str | None,
    image_url: str | None,
) -> None:
    """Fill known JustWatch metadata without replacing existing values with NULL."""
    conn.execute(
        """
        UPDATE watchlist_items
        SET justwatch_url = COALESCE(?, justwatch_url),
            image_url = COALESCE(?, image_url)
        WHERE trakt_id = ? AND media_type = ?
        """,
        (justwatch_url, image_url, trakt_id, media_type),
    )
    conn.commit()


def get_current_watchlist_movies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return cached movie metadata for exact matching during an Amazon audit."""
    rows = conn.execute(
        """
        SELECT trakt_id, title, year, trakt_slug, tmdb_id, imdb_id
        FROM watchlist_items
        WHERE media_type = 'movie' AND in_watchlist = 1
        """
    ).fetchall()
    return [
        {
            "trakt_id": int(row[0]),
            "trakt_title": str(row[1]),
            "trakt_year": row[2],
            "trakt_slug": row[3] or "",
            "tmdb_id": row[4],
            "imdb_id": row[5],
        }
        for row in rows
    ]


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
