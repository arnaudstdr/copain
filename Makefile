.PHONY: install lock run test lint typecheck format docker-build docker-up docker-down clean

install:
	uv sync
	uv run pre-commit install

lock:
	uv lock

run:
	uv run python -m bot.main

test:
	uv run pytest

lint:
	uv run ruff check bot tests

format:
	uv run ruff format bot tests
	uv run ruff check --fix bot tests

typecheck:
	uv run mypy bot

build:
	docker compose build --no-cache

up:
	docker compose up -d

down:
	docker compose down

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
