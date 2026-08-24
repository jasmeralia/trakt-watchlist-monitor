import smtplib
from unittest.mock import Mock

import pytest
from trakt_watchlist_monitor import pricing
from trakt_watchlist_monitor.pricing import meets_discount_threshold, select_best_quality


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestSelectBestQuality:
    def test_prefers_uhd_over_hd_and_sd(self) -> None:
        prices = [
            {"quality": "SD", "price": 9.99},
            {"quality": "HD", "price": 12.99},
            {"quality": "UHD", "price": 14.99},
        ]
        result = select_best_quality(prices)
        assert result is not None
        assert result["quality"] == "UHD"

    def test_prefers_hd_over_sd(self) -> None:
        prices = [
            {"quality": "SD", "price": 9.99},
            {"quality": "HD", "price": 12.99},
        ]
        result = select_best_quality(prices)
        assert result is not None
        assert result["quality"] == "HD"

    def test_single_sd_entry(self) -> None:
        prices = [{"quality": "SD", "price": 9.99}]
        result = select_best_quality(prices)
        assert result is not None
        assert result["quality"] == "SD"

    def test_empty_list_returns_none(self) -> None:
        assert select_best_quality([]) is None

    def test_prefers_lowest_price_within_same_quality(self) -> None:
        prices = [
            {"quality": "HD", "price": 12.99},
            {"quality": "HD", "price": 7.99},
            {"quality": "SD", "price": 4.99},
        ]
        result = select_best_quality(prices)
        assert result is not None
        assert result["quality"] == "HD"
        assert result["price"] == 7.99


class TestMeetsDiscountThreshold:
    def test_above_threshold(self) -> None:
        assert meets_discount_threshold(10.00, 7.00, 20.0) is True  # 30% drop

    def test_exactly_at_threshold(self) -> None:
        assert meets_discount_threshold(10.00, 8.00, 20.0) is True  # exactly 20%

    def test_below_threshold(self) -> None:
        assert meets_discount_threshold(10.00, 9.00, 20.0) is False  # 10% drop

    def test_no_discount(self) -> None:
        assert meets_discount_threshold(10.00, 10.00, 20.0) is False


class TestCheckPrices:
    def test_sends_alert_when_threshold_is_met(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                        "trakt_slug": "example-movie",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        drops = send_digest.call_args.args[0]
        assert len(drops) == 1
        assert drops[0].trakt_id == 123
        assert drops[0].current_price == 7.99
        assert drops[0].trakt_url == "https://trakt.tv/movies/example-movie"
        log_notification.assert_called_once_with(conn, 123, "movie", "HD", 7.99, 12.99)
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 7.99, "USD")
        assert conn.closed is True

    def test_does_not_send_alert_when_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 11.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        log_notification.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 11.99, "USD")
        assert conn.closed is True

    def test_first_observation_stores_price_without_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=None))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        log_notification.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 7.99, "USD")
        assert conn.closed is True

    def test_does_not_send_duplicate_alert_when_already_notified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=True))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        log_notification.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 7.99, "USD")
        assert conn.closed is True

    def test_skips_item_with_no_justwatch_prices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch, "get_amazon_prices", Mock(return_value=([], None, None))
        )
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        upsert_price.assert_not_called()
        assert conn.closed is True

    def test_skips_item_with_unexpected_currency(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = FakeConnection()
        get_last_price = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "EUR"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", get_last_price)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        get_last_price.assert_not_called()
        upsert_price.assert_not_called()
        assert "Unexpected currency 'EUR' for trakt_id 123; skipping" in capsys.readouterr().err
        assert conn.closed is True

    def test_skips_item_without_tmdb_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        get_amazon_prices = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": None,
                    }
                ]
            ),
        )
        monkeypatch.setattr(pricing.justwatch, "get_amazon_prices", get_amazon_prices)
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        get_amazon_prices.assert_not_called()
        send_digest.assert_not_called()
        upsert_price.assert_not_called()
        assert conn.closed is True

    def test_stores_price_increase_without_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 14.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        log_notification.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 14.99, "USD")
        assert conn.closed is True

    def test_sends_alert_for_first_observation_below_sale_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Cheap Movie",
                        "tmdb_id": 456,
                        "trakt_slug": "cheap-movie",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 3.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=None))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        drops = send_digest.call_args.args[0]
        assert len(drops) == 1
        assert drops[0].trakt_id == 123
        assert drops[0].current_price == 3.99
        assert drops[0].last_price == 0.0
        log_notification.assert_called_once_with(conn, 123, "movie", "HD", 3.99, 0.0)
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 3.99, "USD")
        assert conn.closed is True

    def test_no_alert_for_first_observation_above_sale_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 9.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=None))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 9.99, "USD")
        assert conn.closed is True

    def test_sends_alert_when_price_below_threshold_regardless_of_discount_percent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                        "trakt_slug": "example-movie",
                    }
                ]
            ),
        )
        # Only a 5% drop, well below 20% threshold, but price is below $5
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 4.74, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=4.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        drops = send_digest.call_args.args[0]
        assert len(drops) == 1
        assert drops[0].current_price == 4.74
        assert conn.closed is True

    def test_sends_alert_after_price_increase_resets_sale_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                        "trakt_slug": "example-movie",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 4.99, "currency": "USD"}], None, None)),
        )
        # last_price is 5.99 — price went up after previous $4.99 notification, now back below $5
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=5.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        drops = send_digest.call_args.args[0]
        assert len(drops) == 1
        assert drops[0].current_price == 4.99
        assert conn.closed is True

    def test_zero_last_price_stores_price_without_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        was_notified = Mock(return_value=False)
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=0.0))
        monkeypatch.setattr(pricing.db, "was_notified", was_notified)
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_not_called()
        log_notification.assert_not_called()
        was_notified.assert_not_called()
        upsert_price.assert_called_once_with(conn, 123, "movie", "HD", 7.99, "USD")
        assert conn.closed is True

    def test_notification_failure_does_not_skip_price_upsert(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock(side_effect=smtplib.SMTPException("smtp failed"))
        log_notification = Mock()
        upsert_price = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        log_notification.assert_not_called()
        upsert_price.assert_not_called()
        assert "Failed to send digest: smtp failed" in capsys.readouterr().err
        assert conn.closed is True

    def test_add_to_sale_list_called_for_qualifying_drop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        add_to_sale_list = Mock()
        remove_from_sale_list = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.settings, "sale_list_slug", "on-sale")
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                        "trakt_slug": "example-movie",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", Mock())
        monkeypatch.setattr(pricing.db, "log_notification", Mock())
        monkeypatch.setattr(pricing.db, "upsert_price", Mock())
        monkeypatch.setattr(pricing.trakt, "add_to_sale_list", add_to_sale_list)
        monkeypatch.setattr(pricing.trakt, "remove_from_sale_list", remove_from_sale_list)

        pricing.check_prices()

        add_to_sale_list.assert_called_once_with(123, "movie")
        remove_from_sale_list.assert_not_called()

    def test_remove_from_sale_list_called_when_price_increases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = FakeConnection()
        add_to_sale_list = Mock()
        remove_from_sale_list = Mock()

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.settings, "sale_price_threshold", 5.0)
        monkeypatch.setattr(pricing.settings, "sale_list_slug", "on-sale")
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 456,
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            pricing.justwatch,
            "get_amazon_prices",
            Mock(return_value=([{"quality": "HD", "price": 14.99, "currency": "USD"}], None, None)),
        )
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", Mock())
        monkeypatch.setattr(pricing.db, "log_notification", Mock())
        monkeypatch.setattr(pricing.db, "upsert_price", Mock())
        monkeypatch.setattr(pricing.trakt, "add_to_sale_list", add_to_sale_list)
        monkeypatch.setattr(pricing.trakt, "remove_from_sale_list", remove_from_sale_list)

        pricing.check_prices()

        remove_from_sale_list.assert_called_once_with(123, "movie")
        add_to_sale_list.assert_not_called()

    def test_item_failure_continues_to_next_item(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = FakeConnection()
        send_digest = Mock()
        log_notification = Mock()
        upsert_price = Mock()

        def get_amazon_prices(
            tmdb_id: int, _media_type: str, _title: str
        ) -> tuple[list[dict[str, object]], None, None]:
            if tmdb_id == 456:
                raise ValueError("bad justwatch response")
            return [{"quality": "HD", "price": 7.99, "currency": "USD"}], None, None

        monkeypatch.setattr(pricing.settings, "db_path", ":memory:")
        monkeypatch.setattr(pricing.settings, "discount_threshold_percent", 20.0)
        monkeypatch.setattr(pricing.db, "init_db", Mock(return_value=conn))
        monkeypatch.setattr(
            pricing.trakt,
            "get_effective_watchlist",
            Mock(
                return_value=[
                    {
                        "trakt_id": 123,
                        "media_type": "movie",
                        "title": "Broken Movie",
                        "tmdb_id": 456,
                    },
                    {
                        "trakt_id": 789,
                        "media_type": "movie",
                        "title": "Example Movie",
                        "tmdb_id": 987,
                    },
                ]
            ),
        )
        monkeypatch.setattr(pricing.justwatch, "get_amazon_prices", get_amazon_prices)
        monkeypatch.setattr(pricing.db, "get_last_price", Mock(return_value=12.99))
        monkeypatch.setattr(pricing.db, "was_notified", Mock(return_value=False))
        monkeypatch.setattr(pricing.notify, "send_digest", send_digest)
        monkeypatch.setattr(pricing.db, "log_notification", log_notification)
        monkeypatch.setattr(pricing.db, "upsert_price", upsert_price)

        pricing.check_prices()

        send_digest.assert_called_once()
        drops = send_digest.call_args.args[0]
        assert len(drops) == 1
        assert drops[0].trakt_id == 789
        log_notification.assert_called_once_with(conn, 789, "movie", "HD", 7.99, 12.99)
        upsert_price.assert_called_once_with(conn, 789, "movie", "HD", 7.99, "USD")
        assert "Failed to process price for 123: bad justwatch response" in capsys.readouterr().err
        assert conn.closed is True
