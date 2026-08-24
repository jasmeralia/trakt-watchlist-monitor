.PHONY: venv lintfix lint test clean

venv:
	uv sync

lintfix: venv
	uv run ruff check --fix trakt_watchlist_monitor/ tests/ scripts/
	uv run ruff format trakt_watchlist_monitor/ tests/ scripts/

lint: venv
	uv run ruff check trakt_watchlist_monitor/ tests/ scripts/
	uv run mypy trakt_watchlist_monitor/ scripts/
	uv run pylint trakt_watchlist_monitor/ scripts/
	@if find . -maxdepth 3 -name "*.sh" | grep -q .; then \
	  find . -maxdepth 3 -name "*.sh" -exec shellcheck {} +; \
	fi
	@if [ -f Dockerfile ]; then hadolint Dockerfile; fi

test: venv
	uv run pytest tests/ -v --tb=short --cov=trakt_watchlist_monitor --cov-report=term-missing --cov-report=xml:coverage.xml

clean:
	rm -rf .venv __pycache__ .mypy_cache .pytest_cache .ruff_cache
