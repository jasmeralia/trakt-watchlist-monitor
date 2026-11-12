FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY trakt_watchlist_monitor/ trakt_watchlist_monitor/

RUN useradd --system --uid 1001 appuser
USER 1001

ENV PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=30m --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

CMD ["python", "-m", "trakt_watchlist_monitor"]
