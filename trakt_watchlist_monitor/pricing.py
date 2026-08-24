# Pricing evaluation logic

import logging
import smtplib
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

from . import db, justwatch, notify, trakt
from .config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceDrop:  # pylint: disable=too-many-instance-attributes
    item: dict[str, Any]
    trakt_id: int
    media_type: str
    quality: str
    current_price: float
    currency: str
    last_price: float
    image_url: str | None = None
    trakt_url: str | None = None
    jw_url: str | None = None


def select_best_quality(prices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Given a list of price entries, return highest quality only.
    Priority: UHD > HD > SD
    """
    priority = {"UHD": 3, "HD": 2, "SD": 1}
    return min(
        prices,
        key=lambda p: (-priority.get(p["quality"], 0), float(p["price"])),
        default=None,
    )


def meets_discount_threshold(original: float, current: float, percent: float) -> bool:
    drop = (original - current) / original * 100
    return drop >= percent


def _new_stats() -> dict[str, int]:
    return {
        "watchlist_items": 0,
        "invalid_items": 0,
        "missing_tmdb_id": 0,
        "without_amazon_offer": 0,
        "unexpected_currency": 0,
        "first_observations": 0,
        "prices_recorded": 0,
        "alerts_sent": 0,
        "alert_failures": 0,
        "item_errors": 0,
    }


def _format_prices(prices: list[dict[str, Any]]) -> str:
    if not prices:
        return "[]"
    return "[" + ", ".join(_format_price(price) for price in prices) + "]"


def _format_price(price: dict[str, Any]) -> str:
    return (
        f"quality={price.get('quality')!r} "
        f"price={price.get('price')!r} "
        f"currency={price.get('currency')!r}"
    )


def _log_stats(stats: dict[str, int]) -> None:
    logger.info(
        "Price check complete: watchlist_items=%d invalid_items=%d missing_tmdb_id=%d "
        "without_amazon_offer=%d unexpected_currency=%d first_observations=%d "
        "prices_recorded=%d alerts_sent=%d alert_failures=%d item_errors=%d",
        stats["watchlist_items"],
        stats["invalid_items"],
        stats["missing_tmdb_id"],
        stats["without_amazon_offer"],
        stats["unexpected_currency"],
        stats["first_observations"],
        stats["prices_recorded"],
        stats["alerts_sent"],
        stats["alert_failures"],
        stats["item_errors"],
    )


def _extract_item_fields(
    item: dict[str, Any], stats: dict[str, int]
) -> tuple[int, str, int] | None:
    trakt_id = item.get("trakt_id")
    media_type = item.get("media_type")
    tmdb_id = item.get("tmdb_id")
    if not isinstance(trakt_id, int) or not isinstance(media_type, str):
        stats["invalid_items"] += 1
        return None
    if not isinstance(tmdb_id, int):
        stats["missing_tmdb_id"] += 1
        return None
    return trakt_id, media_type, tmdb_id


def _process_watchlist_item(  # pylint: disable=too-many-locals
    conn: sqlite3.Connection,
    item: dict[str, Any],
    item_fields: tuple[int, str, int],
    stats: dict[str, int],
    qualified_drops: list[PriceDrop],
) -> None:
    trakt_id, media_type, tmdb_id = item_fields
    logger.debug(
        "Polling watchlist item: trakt_id=%d media_type=%s tmdb_id=%d title=%r",
        trakt_id,
        media_type,
        tmdb_id,
        item.get("title", ""),
    )

    title = str(item.get("title", ""))
    prices, image_url, jw_url = justwatch.get_amazon_prices(tmdb_id, media_type, title)
    logger.debug(
        "Prices seen for trakt_id=%d media_type=%s tmdb_id=%d: offer_count=%d offers=%s",
        trakt_id,
        media_type,
        tmdb_id,
        len(prices),
        _format_prices(prices),
    )

    best_price = select_best_quality(prices)
    if best_price is None:
        stats["without_amazon_offer"] += 1
        logger.debug("No Amazon buy offer found for trakt_id=%d", trakt_id)
        return

    quality = str(best_price["quality"])
    current_price = float(best_price["price"])
    currency = str(best_price.get("currency", "USD"))
    logger.debug(
        "Selected best price for trakt_id=%d: quality=%s price=%.2f currency=%s",
        trakt_id,
        quality,
        current_price,
        currency,
    )
    if currency != "USD":
        stats["unexpected_currency"] += 1
        print(
            f"Unexpected currency {currency!r} for trakt_id {trakt_id}; skipping",
            file=sys.stderr,
        )
        return

    last_price = db.get_last_price(conn, trakt_id, media_type, quality)
    logger.debug(
        "Stored price for trakt_id=%d media_type=%s quality=%s: last_price=%r",
        trakt_id,
        media_type,
        quality,
        last_price,
    )

    slug = item.get("trakt_slug")
    trakt_url = f"https://trakt.tv/{media_type}s/{slug}" if isinstance(slug, str) and slug else None

    on_sale = current_price < settings.sale_price_threshold or (
        last_price is not None
        and last_price != 0.0
        and current_price < last_price
        and meets_discount_threshold(last_price, current_price, settings.discount_threshold_percent)
    )
    if on_sale:
        trakt.add_to_sale_list(trakt_id, media_type)
    else:
        trakt.remove_from_sale_list(trakt_id, media_type)

    if last_price is None:
        stats["first_observations"] += 1
        if current_price < settings.sale_price_threshold:
            logger.debug(
                "First observation below sale threshold for trakt_id=%d: price=%.2f",
                trakt_id,
                current_price,
            )
            price_drop = PriceDrop(
                item=item,
                trakt_id=trakt_id,
                media_type=media_type,
                quality=quality,
                current_price=current_price,
                currency=currency,
                last_price=0.0,
                image_url=image_url,
                trakt_url=trakt_url,
                jw_url=jw_url,
            )
            if _qualify_price_drop(conn, price_drop):
                qualified_drops.append(price_drop)
                return
        stats["prices_recorded"] += 1
        logger.debug("Recording first observed price for trakt_id=%d", trakt_id)
        db.upsert_price(conn, trakt_id, media_type, quality, current_price, currency)
        return

    if last_price != 0.0 and current_price < last_price:
        price_drop = PriceDrop(
            item=item,
            trakt_id=trakt_id,
            media_type=media_type,
            quality=quality,
            current_price=current_price,
            currency=currency,
            last_price=last_price,
            image_url=image_url,
            trakt_url=trakt_url,
            jw_url=jw_url,
        )
        if _qualify_price_drop(conn, price_drop):
            qualified_drops.append(price_drop)
            return
    else:
        logger.debug(
            "No qualifying price drop for trakt_id=%d: last_price=%.2f current_price=%.2f",
            trakt_id,
            last_price,
            current_price,
        )

    stats["prices_recorded"] += 1
    db.upsert_price(conn, trakt_id, media_type, quality, current_price, currency)


def _qualify_price_drop(
    conn: sqlite3.Connection,
    price_drop: PriceDrop,
) -> bool:
    if price_drop.current_price < settings.sale_price_threshold:
        logger.debug(
            "Price below sale threshold for trakt_id=%d: current_price=%.2f threshold=%.2f",
            price_drop.trakt_id,
            price_drop.current_price,
            settings.sale_price_threshold,
        )
    else:
        drop_percent = (
            (price_drop.last_price - price_drop.current_price) / price_drop.last_price * 100
        )
        logger.debug(
            "Price drop seen for trakt_id=%d: last_price=%.2f current_price=%.2f drop_percent=%.1f",
            price_drop.trakt_id,
            price_drop.last_price,
            price_drop.current_price,
            drop_percent,
        )
        if not meets_discount_threshold(
            price_drop.last_price,
            price_drop.current_price,
            settings.discount_threshold_percent,
        ):
            logger.debug(
                "Price drop below threshold for trakt_id=%d: threshold_percent=%.1f",
                price_drop.trakt_id,
                settings.discount_threshold_percent,
            )
            return False
    last_price_for_reset = price_drop.last_price if price_drop.last_price != 0.0 else None
    if db.was_notified(
        conn,
        price_drop.trakt_id,
        price_drop.media_type,
        price_drop.quality,
        price_drop.current_price,
        last_price_for_reset,
    ):
        logger.debug(
            "Skipping duplicate alert for trakt_id=%d price=%.2f",
            price_drop.trakt_id,
            price_drop.current_price,
        )
        return False
    return True


def check_prices() -> None:
    conn = db.init_db(settings.db_path)
    stats = _new_stats()
    qualified_drops: list[PriceDrop] = []
    logger.info("Checking watchlist prices")
    # pylint: disable=too-many-nested-blocks
    try:
        watchlist = trakt.get_effective_watchlist()
        stats["watchlist_items"] = len(watchlist)
        for item in watchlist:
            item_fields = _extract_item_fields(item, stats)
            if item_fields is None:
                continue

            try:
                _process_watchlist_item(conn, item, item_fields, stats, qualified_drops)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                stats["item_errors"] += 1
                trakt_id = item_fields[0]
                print(f"Failed to process price for {trakt_id}: {exc}", file=sys.stderr)
                continue

        if qualified_drops:
            try:
                notify.send_digest(qualified_drops)
                for drop in qualified_drops:
                    stats["prices_recorded"] += 1
                    db.upsert_price(
                        conn,
                        drop.trakt_id,
                        drop.media_type,
                        drop.quality,
                        drop.current_price,
                        drop.currency,
                    )
                    db.log_notification(
                        conn,
                        drop.trakt_id,
                        drop.media_type,
                        drop.quality,
                        drop.current_price,
                        drop.last_price,
                    )
                stats["alerts_sent"] += len(qualified_drops)
            except (OSError, smtplib.SMTPException) as exc:
                stats["alert_failures"] += len(qualified_drops)
                print(f"Failed to send digest: {exc}", file=sys.stderr)
    finally:
        conn.close()
    _log_stats(stats)
