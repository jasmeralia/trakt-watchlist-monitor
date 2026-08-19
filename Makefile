VENV   := .venv
PIP    := $(VENV)/bin/pip
RUFF   := $(VENV)/bin/ruff
MYPY   := $(VENV)/bin/mypy
PYLINT := $(VENV)/bin/pylint
PYTEST := $(VENV)/bin/pytest

.PHONY: venv lintfix lint test clean

.venv/bin/activate: requirements.txt requirements-dev.txt
	python3 -m venv --clear $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

venv: .venv/bin/activate

lintfix: .venv/bin/activate
	$(RUFF) check --fix app/ tests/ scripts/
	$(RUFF) format app/ tests/ scripts/

lint: .venv/bin/activate
	$(RUFF) check app/ tests/ scripts/
	$(MYPY) app/ scripts/
	$(PYLINT) app/ scripts/
	@if find . -maxdepth 3 -name "*.sh" | grep -q .; then \
	  find . -maxdepth 3 -name "*.sh" -exec shellcheck {} +; \
	fi
	@if [ -f Dockerfile ]; then hadolint Dockerfile; fi

test: .venv/bin/activate
	$(PYTEST) tests/ -v --tb=short --cov=app --cov-report=term-missing --cov-report=xml:coverage.xml

clean:
	rm -rf $(VENV) __pycache__ .mypy_cache .pytest_cache .ruff_cache
