import argparse
import logging
import time
from pathlib import Path

from . import db
from .config import settings
from .pricing import check_prices

logger = logging.getLogger(__name__)


def _maybe_reset_alerts() -> None:
    flag_path = Path(settings.db_path).parent / "reset_alerts"
    if not flag_path.exists():
        return
    flag_path.unlink()
    conn = db.init_db(settings.db_path)
    try:
        counts = db.reset_notification_state(conn)
    finally:
        conn.close()
    logger.info(
        "Alert state reset: %d price(s) restored, %d first-observation price(s) cleared, "
        "%d notification(s) removed.",
        counts["prices_restored"],
        counts["prices_cleared"],
        counts["notifications_cleared"],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trakt watchlist price monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single monitoring cycle and exit instead of looping forever",
    )
    return parser.parse_args()


def _run_cycle() -> None:
    logger.info("Starting monitoring cycle")
    _maybe_reset_alerts()
    check_prices()
    logger.info("Monitoring cycle complete")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.once or settings.run_once:
        _run_cycle()
        return

    while True:
        try:
            _run_cycle()
            logger.info("Sleeping for %.2f hours", settings.check_interval_hours)
            time.sleep(settings.check_interval_hours * 3600)
        except Exception:  # pylint: disable=broad-exception-caught
            logging.exception("Monitoring loop failed")
            time.sleep(settings.check_interval_hours * 3600)
