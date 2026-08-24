import sqlite3

from trakt_watchlist_monitor.db import (
    get_last_price,
    init_db,
    log_notification,
    reset_notification_state,
    upsert_price,
    was_notified,
)


def test_init_db_creates_tables() -> None:
    conn = init_db(":memory:")
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
                AND name IN ('price_history', 'notification_log')
            """
        ).fetchall()
    finally:
        conn.close()

    assert {row[0] for row in rows} == {"price_history", "notification_log"}


def test_get_last_price_returns_none_for_unknown_item() -> None:
    conn = init_db(":memory:")
    try:
        assert get_last_price(conn, 1, "movie", "HD") is None
    finally:
        conn.close()


def test_upsert_then_get_returns_price() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 9.99)

        assert get_last_price(conn, 1, "movie", "HD") == 9.99
    finally:
        conn.close()


def test_upsert_replaces_existing_price_for_same_key() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 9.99)
        upsert_price(conn, 1, "movie", "HD", 7.99)

        assert get_last_price(conn, 1, "movie", "HD") == 7.99
    finally:
        conn.close()


def test_was_notified_returns_false_then_true_after_logging() -> None:
    conn = init_db(":memory:")
    try:
        assert was_notified(conn, 1, "movie", "HD", 7.99) is False

        log_notification(conn, 1, "movie", "HD", 7.99, 12.99)

        assert was_notified(conn, 1, "movie", "HD", 7.99) is True
    finally:
        conn.close()


def test_was_notified_returns_true_for_price_below_logged_price() -> None:
    conn = init_db(":memory:")
    try:
        log_notification(conn, 1, "movie", "HD", 10.00, 12.99)

        assert was_notified(conn, 1, "movie", "HD", 7.99) is True
    finally:
        conn.close()


def test_reset_notification_state_restores_price_from_original_price() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 7.99)
        log_notification(conn, 1, "movie", "HD", 7.99, 12.99)

        counts = reset_notification_state(conn)

        assert get_last_price(conn, 1, "movie", "HD") == 12.99
        assert counts["prices_restored"] == 1
        assert counts["prices_cleared"] == 0
        assert counts["notifications_cleared"] == 1
        assert not was_notified(conn, 1, "movie", "HD", 7.99)
    finally:
        conn.close()


def test_reset_notification_state_clears_first_obs_below_threshold() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 3.99)
        log_notification(conn, 1, "movie", "HD", 3.99, 0.0)

        counts = reset_notification_state(conn)

        assert get_last_price(conn, 1, "movie", "HD") is None
        assert counts["prices_restored"] == 0
        assert counts["prices_cleared"] == 1
        assert counts["notifications_cleared"] == 1
    finally:
        conn.close()


def test_reset_notification_state_leaves_unnotified_items_untouched() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 9.99)

        counts = reset_notification_state(conn)

        assert get_last_price(conn, 1, "movie", "HD") == 9.99
        assert counts["prices_restored"] == 0
        assert counts["prices_cleared"] == 0
        assert counts["notifications_cleared"] == 0
    finally:
        conn.close()


def test_reset_notification_state_uses_most_recent_non_zero_original_price() -> None:
    conn = init_db(":memory:")
    try:
        upsert_price(conn, 1, "movie", "HD", 5.99)
        log_notification(conn, 1, "movie", "HD", 9.99, 0.0)
        log_notification(conn, 1, "movie", "HD", 5.99, 9.99)

        counts = reset_notification_state(conn)

        assert get_last_price(conn, 1, "movie", "HD") == 9.99
        assert counts["prices_restored"] == 1
        assert counts["prices_cleared"] == 0
        assert counts["notifications_cleared"] == 2
    finally:
        conn.close()


def test_was_notified_returns_false_when_price_increased_since_notification() -> None:
    conn = init_db(":memory:")
    try:
        log_notification(conn, 1, "movie", "HD", 4.99, 0.0)
        # Price went up to 5.99 since the notification — sale state is reset
        assert was_notified(conn, 1, "movie", "HD", 4.99, last_price=5.99) is False
    finally:
        conn.close()


def test_was_notified_returns_true_when_no_price_increase_since_notification() -> None:
    conn = init_db(":memory:")
    try:
        log_notification(conn, 1, "movie", "HD", 4.99, 0.0)
        # last_price matches the notified price — no increase occurred
        assert was_notified(conn, 1, "movie", "HD", 4.99, last_price=4.99) is True
    finally:
        conn.close()


def test_init_db_returns_open_connection() -> None:
    conn = init_db(":memory:")
    try:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("SELECT 1")
    finally:
        conn.close()
