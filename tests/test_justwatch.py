from collections.abc import Callable
from typing import Any

import pytest
from trakt_watchlist_monitor import justwatch


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> object:
        return self.payload

    def raise_for_status(self) -> None:
        return None


def test_get_amazon_prices_returns_amazon_buy_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(
        [
            _offer("amazon", "BUY", "UHD", "$14.99", "USD"),
            _offer("amazon", "BUY", "HD", "$9.99", "USD"),
        ]
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), calls))

    prices, image_url, jw_url = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert prices == [
        {"quality": "UHD", "price": 14.99, "currency": "USD"},
        {"quality": "HD", "price": 9.99, "currency": "USD"},
    ]
    assert image_url == "https://images.justwatch.com/poster/abc123/s166/movie-title.jpg"
    assert jw_url == "https://www.justwatch.com/us/movie/arrival"
    assert calls[0]["url"] == "https://apis.justwatch.com/graphql"
    assert calls[0]["json"]["operationName"] == "GetPopularTitles"
    assert calls[0]["json"]["variables"]["popularTitlesFilter"] == {
        "objectTypes": ["MOVIE"],
        "searchQuery": "Arrival",
    }


def test_get_amazon_prices_show_uses_show_object_type(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        justwatch.requests,
        "post",
        _post_factory(FakeResponse(_payload([], tmdb_id=12345)), calls),
    )

    prices, _, _ = justwatch.get_amazon_prices(12345, "show", "Example Show")
    assert prices == []

    assert calls[0]["json"]["variables"]["popularTitlesFilter"] == {
        "objectTypes": ["SHOW"],
        "searchQuery": "Example Show",
    }


def test_get_amazon_prices_returns_empty_list_when_title_is_missing() -> None:
    prices, image_url, jw_url = justwatch.get_amazon_prices(329865, "movie", "")
    assert prices == []
    assert image_url is None
    assert jw_url is None


def test_get_amazon_prices_returns_empty_list_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        justwatch.requests,
        "post",
        _post_factory(FakeResponse({"data": {"popularTitles": {"edges": []}}}), calls),
    )

    prices, _, _ = justwatch.get_amazon_prices(329865, "movie", "Missing Movie")
    assert prices == []


def test_get_amazon_prices_ignores_wrong_tmdb_match(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", "$9.99", "USD")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert prices == []


def test_get_amazon_prices_returns_empty_list_for_graphql_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"errors": [{"message": "title lookup failed"}], "data": None}
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert prices == []
    assert "JustWatch GraphQL error: title lookup failed" in capsys.readouterr().err


def test_get_amazon_prices_filters_out_non_amazon_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(
        [
            _offer("itunes", "BUY", "HD", "$7.99", "USD"),
            _offer("amazon", "RENT", "HD", "$3.99", "USD"),
            _offer("amazon", "BUY", "HD", "$9.99", "USD"),
        ]
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), calls))

    prices, _, _ = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert prices == [{"quality": "HD", "price": 9.99, "currency": "USD"}]


def test_get_amazon_prices_parses_string_price(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", "$12.99", "USD")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "HD", "price": 12.99, "currency": "USD"}]


def test_get_amazon_prices_prefers_retail_price_value(monkeypatch: pytest.MonkeyPatch) -> None:
    offer = _offer("amazon", "BUY", "HD", "$999.99", "USD")
    offer["retailPriceValue"] = 9.99
    payload = _payload([offer], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "HD", "price": 9.99, "currency": "USD"}]


def test_get_amazon_prices_parses_comma_decimal_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", "€9,99", "EUR")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "HD", "price": 9.99, "currency": "EUR"}]


def test_get_amazon_prices_parses_thousands_separator_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", "1,299.00", "USD")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "HD", "price": 1299.0, "currency": "USD"}]


def test_get_amazon_prices_parses_numeric_price(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", 9.99, "USD")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "HD", "price": 9.99, "currency": "USD"}]


def test_get_amazon_prices_maps_4k_to_uhd(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([_offer("amazon", "BUY", "_4K", "$19.99", "USD")], tmdb_id=1)
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    prices, _, _ = justwatch.get_amazon_prices(1, "movie", "Example Movie")
    assert prices == [{"quality": "UHD", "price": 19.99, "currency": "USD"}]


def test_get_amazon_prices_returns_poster_url_and_jw_url(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([_offer("amazon", "BUY", "HD", "$9.99", "USD")])
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    _, image_url, jw_url = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert image_url == "https://images.justwatch.com/poster/abc123/s166/movie-title.jpg"
    assert jw_url == "https://www.justwatch.com/us/movie/arrival"


def test_get_amazon_prices_handles_missing_poster_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(
        [_offer("amazon", "BUY", "HD", "$9.99", "USD")], poster_url=None, full_path=None
    )
    monkeypatch.setattr(justwatch.requests, "post", _post_factory(FakeResponse(payload), []))

    _, image_url, jw_url = justwatch.get_amazon_prices(329865, "movie", "Arrival")
    assert image_url is None
    assert jw_url is None


def _post_factory(
    response: FakeResponse, calls: list[dict[str, Any]]
) -> Callable[..., FakeResponse]:
    def post(*_args: Any, **kwargs: Any) -> FakeResponse:  # noqa: ANN401
        calls.append({"url": _args[0], **kwargs})
        return response

    return post


def _payload(
    offers: list[dict[str, Any]],
    tmdb_id: int = 329865,
    poster_url: str | None = "/poster/abc123/{profile}/movie-title.{format}",
    full_path: str | None = "/us/movie/arrival",
) -> dict[str, Any]:
    return {
        "data": {
            "popularTitles": {
                "edges": [
                    {
                        "node": {
                            "content": {
                                "externalIds": {"tmdbId": str(tmdb_id)},
                                "posterUrl": poster_url,
                                "fullPath": full_path,
                            },
                            "offers": offers,
                        }
                    }
                ]
            }
        }
    }


def _offer(
    technical_name: str,
    monetization_type: str,
    presentation_type: str,
    retail_price: float | str,
    currency: str,
) -> dict[str, Any]:
    return {
        "monetizationType": monetization_type,
        "presentationType": presentation_type,
        "retailPrice": retail_price,
        "retailPriceValue": retail_price if isinstance(retail_price, float) else None,
        "currency": currency,
        "package": {"technicalName": technical_name},
    }
