import time

from .config import settings

_LAST_REQUEST_AT: float | None = None


def wait_between_api_requests() -> None:
    interval = settings.api_request_interval_seconds
    if interval <= 0:
        return

    global _LAST_REQUEST_AT  # pylint: disable=global-statement
    now = time.monotonic()
    if _LAST_REQUEST_AT is not None:
        elapsed = now - _LAST_REQUEST_AT
        if elapsed < interval:
            time.sleep(interval - elapsed)
            now = time.monotonic()
    _LAST_REQUEST_AT = now
