.PHONY: install dev test lint format run demo docker-up docker-down docs docs-check board hooks check eval

install:
	uv venv .venv -p 3.11 && uv pip install -p .venv -e ".[dev]"
	git config core.hooksPath .githooks

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

# ---- 칸반 / 문서 자동 연동 (CLAUDE.md) ----
board:
	.venv/bin/python -m aiops.kanban.cli brief

docs:
	.venv/bin/python -m aiops.kanban.cli docs sync

docs-check:
	.venv/bin/python -m aiops.kanban.cli docs check

hooks:
	git config core.hooksPath .githooks

check: lint test docs docs-check  # 로컬은 동기화 후 검증 (CI 는 동기화 없이 엄격 비교)

# ---- 평가 (M1) ----
eval:
	.venv/bin/python scripts/eval_retrieval.py
	.venv/bin/python scripts/eval_anomaly.py
	.venv/bin/python scripts/eval_situation.py
	.venv/bin/python scripts/eval_rca.py --seeds 10
	.venv/bin/python scripts/run_evals.py
