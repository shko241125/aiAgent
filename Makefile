.PHONY: install dev test lint format run demo docker-up docker-down

install:
	uv venv .venv -p 3.11 && uv pip install -p .venv -e ".[dev]"

dev:
	uv pip install -p .venv -e ".[dev,agents,ml,vector,postgres]"

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check src tests scripts && .venv/bin/ruff format --check src tests scripts

format:
	.venv/bin/ruff check --fix src tests scripts && .venv/bin/ruff format src tests scripts

run:
	.venv/bin/uvicorn aiops.main:app --reload --app-dir src

demo:
	.venv/bin/python scripts/demo_incident.py

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
