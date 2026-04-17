.PHONY: install run test lint typecheck format docker-build docker-up docker-down clean

PYTHON ?= python3.12
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(VENV)/bin/pre-commit install

run:
	$(PY) -m bot.main

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check bot tests

format:
	$(VENV)/bin/ruff format bot tests
	$(VENV)/bin/ruff check --fix bot tests

typecheck:
	$(VENV)/bin/mypy bot

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
