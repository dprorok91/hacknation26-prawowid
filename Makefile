.PHONY: setup db-up db-down serve worker test lint smoke
setup:
	bash scripts/setup.sh
db-up:
	docker compose --env-file .env up -d postgres
db-down:
	docker compose --env-file .env down
serve:
	uv run --locked uvicorn prawdowid.main:app --reload --port 8000 --no-access-log
worker:
	uv run --locked python -m prawdowid.worker
test:
	uv run --locked pytest -q
lint:
	uv run --locked ruff check .
smoke:
	bash scripts/smoke.sh
