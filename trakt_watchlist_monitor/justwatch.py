import re
import sys
from typing import Any

import requests

from . import rate_limit

GRAPHQL_URL = "https://apis.justwatch.com/graphql"
# "amazon" is the verified technicalName for Amazon buy/rent offers.
# Amazon-branded channel packages (e.g. amazonscreambox, amazoncineverse) are distinct
# and intentionally excluded — those are subscription add-on channels, not direct buys.
AMAZON_PACKAGES = {"amazon"}

PriceOffer = dict[str, str | float]
JustWatchResult = tuple[list[PriceOffer], str | None, str | None]


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.justwatch.com",
}


def get_amazon_prices(tmdb_id: int, media_type: str, title: str) -> JustWatchResult:
    if not title:
        return [], None, None
    rate_limit.wait_between_api_requests()
    response = requests.post(
        GRAPHQL_URL,
        headers=_HEADERS,
        json={
            "operationName": "GetPopularTitles",
            "query": _POPULAR_TITLES_QUERY,
            "variables": _popular_titles_variables(media_type, title),
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        return [], None, None
    if "errors" in payload:
        print(
            f"JustWatch GraphQL error: {_graphql_error_message(payload['errors'])}",
            file=sys.stderr,
        )
        return [], None, None

    offers, image_url, jw_url = _offers_for_matching_title(payload, tmdb_id)

    prices = [
        price
        for offer in offers
        if isinstance(offer, dict)
        if (price := _amazon_buy_price(offer)) is not None
    ]
    return prices, image_url, jw_url


def _popular_titles_variables(media_type: str, title: str) -> dict[str, object]:
    return {
        "country": "US",
        "first": 10,
        "language": "en",
        "platform": "WEB",
        "popularTitlesFilter": {
            "objectTypes": [_object_type(media_type)],
            "searchQuery": title,
        },
    }


def _object_type(media_type: str) -> str:
    if media_type == "show":
        return "SHOW"
    return "MOVIE"


def _offers_for_matching_title(
    payload: dict[Any, Any], tmdb_id: int
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return [], None, None

    popular_titles = data.get("popularTitles")
    if not isinstance(popular_titles, dict):
        return [], None, None

    edges = popular_titles.get("edges")
    if not isinstance(edges, list):
        return [], None, None

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict) or _node_tmdb_id(node) != tmdb_id:
            continue
        offers = node.get("offers")
        offer_list = (
            [offer for offer in offers if isinstance(offer, dict)]
            if isinstance(offers, list)
            else []
        )
        image_url, jw_url = _node_content_metadata(node)
        return offer_list, image_url, jw_url
    return [], None, None


def _node_content_metadata(node: dict[Any, Any]) -> tuple[str | None, str | None]:
    content = node.get("content")
    if not isinstance(content, dict):
        return None, None
    image_url = _build_poster_url(content.get("posterUrl"))
    full_path = content.get("fullPath")
    jw_url = (
        f"https://www.justwatch.com{full_path}"
        if isinstance(full_path, str) and full_path
        else None
    )
    return image_url, jw_url


def _build_poster_url(poster_url: object) -> str | None:
    if not isinstance(poster_url, str) or not poster_url:
        return None
    url = poster_url.replace("{profile}", "s166").replace("{format}", "jpg")
    if url.startswith("/"):
        url = "https://images.justwatch.com" + url
    return url


def _node_tmdb_id(node: dict[Any, Any]) -> int | None:
    content = node.get("content")
    if not isinstance(content, dict):
        return None
    external_ids = content.get("externalIds")
    if not isinstance(external_ids, dict):
        return None
    raw_tmdb_id = external_ids.get("tmdbId")
    try:
        return int(str(raw_tmdb_id))
    except (TypeError, ValueError):
        return None


def _amazon_buy_price(offer: dict[Any, Any]) -> PriceOffer | None:
    if str(offer.get("monetizationType", "")).lower() != "buy":
        return None

    package = offer.get("package")
    if not isinstance(package, dict):
        return None

    technical_name = package.get("technicalName")
    if technical_name not in AMAZON_PACKAGES:
        return None

    quality = _quality(offer.get("presentationType"))
    price = _parse_price(offer.get("retailPriceValue"))
    if price is None:
        price = _parse_price(offer.get("retailPrice"))
    currency = offer.get("currency")
    if quality is None or price is None or not isinstance(currency, str):
        return None

    return {"quality": quality, "price": price, "currency": currency}


def _quality(presentation_type: object) -> str | None:
    if not isinstance(presentation_type, str):
        return None
    # GraphQL enums can't start with a digit, so 4K may be returned as "_4K"
    normalized = presentation_type.upper().lstrip("_")
    if normalized in {"4K", "UHD"}:
        return "UHD"
    if normalized in {"HD", "SD"}:
        return normalized
    return None


def _parse_price(raw: object) -> float | None:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    # retailPrice is locale-formatted, e.g. "$9.99", "€9,99", "1,299.00".
    normalized = re.sub(r"[^\d.,]", "", raw)
    if not normalized:
        return None
    if re.search(r",\d{2}$", normalized):
        whole, cents = normalized.rsplit(",", maxsplit=1)
        normalized = f"{whole}.{cents}".replace(",", "")
    else:
        normalized = normalized.replace(",", "")
    try:
        return float(normalized)
    except ValueError:
        return None


def _graphql_error_message(errors: object) -> str:
    if isinstance(errors, list) and errors:
        first_error = errors[0]
        if isinstance(first_error, dict):
            message = first_error.get("message")
            if isinstance(message, str):
                return message
        if isinstance(first_error, str):
            return first_error
    return "unknown error"


# Search results expose TMDB external IDs and offer details in the same response. The older
# node(id: "tm<tmdb_id>") path resolves many titles but returns empty offer lists.
_POPULAR_TITLES_QUERY = """
query GetPopularTitles(
  $country: Country!
  $first: Int!
  $language: Language!
  $platform: Platform!
  $popularTitlesFilter: TitleFilter
) {
  popularTitles(
    country: $country
    filter: $popularTitlesFilter
    first: $first
    sortBy: POPULAR
  ) {
    edges {
      node {
        content(country: $country, language: $language) {
          externalIds {
            tmdbId
          }
          posterUrl
          fullPath
        }
        offers(country: $country, platform: $platform) {
          monetizationType
          presentationType
          retailPrice(language: $language)
          retailPriceValue
          currency
          package {
            technicalName
          }
        }
      }
    }
  }
}
"""
