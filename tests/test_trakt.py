from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from trakt_watchlist_monitor import trakt
from trakt_watchlist_monitor.config import settings


class FakeResponse:
    def __init__(
        self, payload: object, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> object:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:  # noqa: ANN401
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> FakeSession:
        self.calls += 1
        return self.session


def test_get_watchlist_fetches_raw_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "type": "movie",
            "movie": {"title": "Arrival", "ids": {"trakt": 1, "tmdb": 329865}},
        }
    ]
    session = FakeSession([FakeResponse(payload)])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    assert trakt.get_watchlist() == payload
    assert session.requests[0]["method"] == "GET"
    assert session.requests[0]["url"] == "https://api.trakt.tv/users/username/watchlist"
    assert session.requests[0]["params"] == {"page": 1, "limit": 100}
    assert session.requests[0]["headers"] == {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": "client-id",
        "Authorization": "Bearer access-token",
    }


def test_get_watchlist_fetches_all_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = [
        {
            "type": "movie",
            "movie": {"title": "Arrival", "ids": {"trakt": 1, "tmdb": 329865}},
        }
    ]
    second_page = [
        {
            "type": "movie",
            "movie": {"title": "Dune", "ids": {"trakt": 2, "tmdb": 438631}},
        }
    ]
    session = FakeSession(
        [
            FakeResponse(first_page, headers={"X-Pagination-Page-Count": "2"}),
            FakeResponse(second_page, headers={"X-Pagination-Page-Count": "2"}),
        ]
    )
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    assert trakt.get_watchlist() == [*first_page, *second_page]
    assert [request["params"] for request in session.requests] == [
        {"page": 1, "limit": 100},
        {"page": 2, "limit": 100},
    ]


def test_get_effective_watchlist_filters_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist = [
        {
            "type": "movie",
            "movie": {"title": "Arrival", "ids": {"trakt": 1, "tmdb": 329865}},
        },
        {
            "type": "show",
            "show": {"title": "Severance", "ids": {"trakt": 2, "tmdb": 95396}},
        },
    ]
    collection_movies = [{"movie": {"title": "Arrival", "ids": {"trakt": 1, "tmdb": 329865}}}]
    session = FakeSession(
        [
            FakeResponse(collection_movies),
            FakeResponse([]),
            FakeResponse(watchlist),
        ]
    )
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    assert trakt.get_effective_watchlist() == [
        {
            "trakt_id": 2,
            "media_type": "show",
            "title": "Severance",
            "tmdb_id": 95396,
            "trakt_slug": None,
        }
    ]


def test_get_effective_watchlist_logs_unsupported_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watchlist = [{"type": "season", "season": {"ids": {"trakt": 3}}}]
    session = FakeSession(
        [
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse(watchlist),
        ]
    )
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    assert trakt.get_effective_watchlist() == []
    assert "Unsupported Trakt watchlist item type: season" in capsys.readouterr().err


def test_get_watchlist_refreshes_token_and_retries_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_access_token = settings.trakt_access_token
    old_refresh_token = settings.trakt_refresh_token
    session = FakeSession(
        [
            FakeResponse({"error": "unauthorized"}, status_code=401),
            FakeResponse([]),
        ]
    )
    refresh_response = FakeResponse(
        {"access_token": "new-access-token", "refresh_token": "new-refresh-token"}
    )
    session_factory = FakeSessionFactory(session)
    monkeypatch.setattr(trakt.requests, "Session", session_factory)
    monkeypatch.setattr(trakt.requests, "post", _post_factory(refresh_response))

    try:
        assert trakt.get_watchlist() == []
        assert session_factory.calls == 1
        assert settings.trakt_access_token == "new-access-token"
        assert settings.trakt_refresh_token == "new-refresh-token"
        assert session.requests[1]["headers"]["Authorization"] == "Bearer new-access-token"
    finally:
        settings.trakt_access_token = old_access_token
        settings.trakt_refresh_token = old_refresh_token


def test_get_watchlist_refresh_persists_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_access_token = settings.trakt_access_token
    old_refresh_token = settings.trakt_refresh_token
    session = FakeSession(
        [
            FakeResponse({"error": "unauthorized"}, status_code=401),
            FakeResponse([]),
        ]
    )
    refresh_response = FakeResponse(
        {
            "access_token": "persisted-access-token",
            "refresh_token": "persisted-refresh-token",
        }
    )
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "prices.db"))
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))
    monkeypatch.setattr(trakt.requests, "post", _post_factory(refresh_response))

    try:
        assert trakt.get_watchlist() == []
        assert (tmp_path / "tokens.env").read_text(encoding="utf-8") == (
            "TRAKT_ACCESS_TOKEN=persisted-access-token\n"
            "TRAKT_REFRESH_TOKEN=persisted-refresh-token\n"
        )
    finally:
        settings.trakt_access_token = old_access_token
        settings.trakt_refresh_token = old_refresh_token


def test_add_to_sale_list_posts_movie_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sale_list_slug", "on-sale")
    session = FakeSession([FakeResponse({"added": {"movies": 1}})])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    trakt.add_to_sale_list(123, "movie")

    assert len(session.requests) == 1
    req = session.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://api.trakt.tv/users/username/lists/on-sale/items"
    assert req["json"] == {"movies": [{"ids": {"trakt": 123}}]}


def test_add_to_sale_list_posts_show_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sale_list_slug", "on-sale")
    session = FakeSession([FakeResponse({"added": {"shows": 1}})])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    trakt.add_to_sale_list(456, "show")

    req = session.requests[0]
    assert req["json"] == {"shows": [{"ids": {"trakt": 456}}]}


def test_remove_from_sale_list_posts_to_remove_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sale_list_slug", "on-sale")
    session = FakeSession([FakeResponse({"deleted": {"movies": 1}})])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    trakt.remove_from_sale_list(123, "movie")

    req = session.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://api.trakt.tv/users/username/lists/on-sale/items/remove"
    assert req["json"] == {"movies": [{"ids": {"trakt": 123}}]}


def test_add_to_sale_list_noop_when_slug_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sale_list_slug", "")
    session = FakeSession([])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    trakt.add_to_sale_list(123, "movie")

    assert len(session.requests) == 0


def test_remove_from_sale_list_noop_when_slug_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sale_list_slug", "")
    session = FakeSession([])
    monkeypatch.setattr(trakt.requests, "Session", _session_factory(session))

    trakt.remove_from_sale_list(123, "movie")

    assert len(session.requests) == 0


def _session_factory(session: FakeSession) -> Callable[[], FakeSession]:
    return lambda: session


def _post_factory(response: FakeResponse) -> Callable[..., FakeResponse]:
    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:  # noqa: ANN401
        return response

    return post
