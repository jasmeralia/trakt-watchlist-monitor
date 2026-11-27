import os
import sys
from typing import Any

import requests

from . import rate_limit
from .config import settings

BASE_URL = "https://api.trakt.tv"
PAGE_LIMIT = 100
PAGE_CAP = 1000


def refresh_token() -> None:
    rate_limit.wait_between_api_requests()
    response = requests.post(
        f"{BASE_URL}/oauth/token",
        json={
            "refresh_token": settings.trakt_refresh_token,
            "client_id": settings.trakt_client_id,
            "client_secret": settings.trakt_client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        },
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    settings.trakt_access_token = str(token_data["access_token"])
    settings.trakt_refresh_token = str(token_data["refresh_token"])
    _persist_tokens()


def _persist_tokens() -> None:
    token_dir = os.path.dirname(settings.db_path) or "."
    token_path = os.path.join(token_dir, "tokens.env")
    try:
        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(f"TRAKT_ACCESS_TOKEN={settings.trakt_access_token}\n")
            token_file.write(f"TRAKT_REFRESH_TOKEN={settings.trakt_refresh_token}\n")
    except OSError as exc:
        print(f"Failed to persist Trakt tokens to {token_path}: {exc}", file=sys.stderr)


def get_watchlist() -> list[dict[str, Any]]:
    return _get(f"/users/{settings.trakt_username}/watchlist")


def get_collection() -> list[dict[str, Any]]:
    movies = _get(f"/users/{settings.trakt_username}/collection/movies")
    shows = _get(f"/users/{settings.trakt_username}/collection/shows")
    return [*movies, *shows]


def get_watchlist_snapshot() -> list[dict[str, Any]]:
    """Fetch and normalize a complete watchlist plus collection membership snapshot."""
    collection_keys: set[tuple[int, str]] = set()
    for item in get_collection():
        key = _item_trakt_key(item)
        if key is None:
            msg = "Invalid item in Trakt collection response"
            raise TypeError(msg)
        collection_keys.add(key)
    raw_watchlist = get_watchlist()
    snapshot = []
    for item in raw_watchlist:
        normalized = _normalize_watchlist_item(item)
        if normalized is not None:
            key = (normalized["trakt_id"], normalized["media_type"])
            snapshot.append({**normalized, "in_collection": key in collection_keys})
    return snapshot


def get_effective_watchlist() -> list[dict[str, Any]]:
    return [item for item in get_watchlist_snapshot() if not item["in_collection"]]


def _get(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    previous_page: list[dict[str, Any]] | None = None
    while True:
        if page > PAGE_CAP:
            raise ValueError(f"Trakt pagination exceeded the {PAGE_CAP}-page safety cap for {path}")
        response = _request_with_refresh(
            "GET",
            path,
            params={"page": page, "limit": PAGE_LIMIT},
        )
        data = response.json()
        if not isinstance(data, list):
            msg = f"Expected list response from Trakt path {path}"
            raise TypeError(msg)
        if any(not isinstance(item, dict) for item in data):
            msg = f"Invalid item in Trakt response from {path} page {page}"
            raise TypeError(msg)
        raw_page_count = response.headers.get("X-Pagination-Page-Count")
        if raw_page_count is not None:
            page_count = _pagination_page_count(response)
            if page_count == 0:
                # An empty list may report zero pages; anything else contradicts the header.
                if page == 1 and not data:
                    break
                msg = f"Trakt reported zero pages on page {page} of a non-empty listing for {path}"
                raise ValueError(msg)
            if page > page_count:
                msg = f"Trakt pagination ended before page {page} of {page_count} for {path}"
                raise ValueError(msg)
            if not data and page < page_count:
                msg = f"Trakt pagination ended before page {page + 1} of {page_count} for {path}"
                raise ValueError(msg)
            if page >= page_count:
                items.extend(data)
                break
        elif len(data) < PAGE_LIMIT:
            items.extend(data)
            break
        elif previous_page is not None and data == previous_page:
            break
        items.extend(data)
        page += 1
        previous_page = data
    return items


def _pagination_page_count(response: requests.Response) -> int:
    raw_page_count = response.headers.get("X-Pagination-Page-Count")
    try:
        page_count = int(raw_page_count or "")
        if page_count < 0:
            raise ValueError
        return page_count
    except ValueError as exc:
        msg = f"Invalid Trakt X-Pagination-Page-Count header: {raw_page_count!r}"
        raise ValueError(msg) from exc


def add_to_sale_list(trakt_id: int, media_type: str) -> None:
    if not settings.sale_list_slug:
        return
    _request_with_refresh(
        "POST",
        f"/users/{settings.trakt_username}/lists/{settings.sale_list_slug}/items",
        json=_list_item_body(trakt_id, media_type),
    )


def remove_from_sale_list(trakt_id: int, media_type: str) -> None:
    if not settings.sale_list_slug:
        return
    _request_with_refresh(
        "POST",
        f"/users/{settings.trakt_username}/lists/{settings.sale_list_slug}/items/remove",
        json=_list_item_body(trakt_id, media_type),
    )


def _list_item_body(trakt_id: int, media_type: str) -> dict[str, Any]:
    key = "movies" if media_type == "movie" else "shows"
    return {key: [{"ids": {"trakt": trakt_id}}]}


def _request_with_refresh(
    method: str,
    path: str,
    params: dict[str, int] | None = None,
    json: dict[str, Any] | None = None,
) -> requests.Response:
    session = requests.Session()
    rate_limit.wait_between_api_requests()
    response = session.request(
        method,
        f"{BASE_URL}{path}",
        headers=_headers(),
        params=params,
        json=json,
        timeout=30,
    )
    if response.status_code != 401:
        response.raise_for_status()
        return response

    refresh_token()
    rate_limit.wait_between_api_requests()
    response = session.request(
        method,
        f"{BASE_URL}{path}",
        headers=_headers(),
        params=params,
        json=json,
        timeout=30,
    )
    response.raise_for_status()
    return response


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": settings.trakt_client_id,
        "Authorization": f"Bearer {settings.trakt_access_token}",
    }


def _normalize_watchlist_item(item: dict[str, Any]) -> dict[str, Any] | None:
    media_type = item.get("type")
    if media_type == "movie":
        media = item.get("movie")
    elif media_type == "show":
        media = item.get("show")
    else:
        print(f"Unsupported Trakt watchlist item type: {media_type}", file=sys.stderr)
        return None

    if not isinstance(media, dict):
        return None

    ids = media.get("ids")
    if not isinstance(ids, dict):
        return None

    trakt_id = ids.get("trakt")
    if not isinstance(trakt_id, int):
        return None

    tmdb_id = ids.get("tmdb")
    slug = ids.get("slug")
    imdb_id = ids.get("imdb")
    listed_at = item.get("listed_at")
    year = media.get("year")
    return {
        "trakt_id": trakt_id,
        "media_type": media_type,
        "title": str(media.get("title", "")),
        "year": year if isinstance(year, int) else None,
        "tmdb_id": tmdb_id if isinstance(tmdb_id, int) else None,
        "trakt_slug": slug if isinstance(slug, str) else None,
        "imdb_id": imdb_id if isinstance(imdb_id, str) else None,
        "listed_at": listed_at if isinstance(listed_at, str) else None,
    }


def _item_trakt_key(item: dict[str, Any]) -> tuple[int, str] | None:
    for media_type in ("movie", "show"):
        media = item.get(media_type)
        if not isinstance(media, dict):
            continue
        ids = media.get("ids")
        if not isinstance(ids, dict):
            continue
        trakt_id = ids.get("trakt")
        if isinstance(trakt_id, int):
            return trakt_id, media_type
    return None


def _item_trakt_id(item: dict[str, Any]) -> int | None:
    for media_type in ("movie", "show"):
        media = item.get(media_type)
        if not isinstance(media, dict):
            continue
        ids = media.get("ids")
        if not isinstance(ids, dict):
            continue

        trakt_id = ids.get("trakt")
        if isinstance(trakt_id, int):
            return trakt_id
    return None
