.PHONY: setup db-up db-down serve worker test lint smoke
setup:
	bash scripts/setup.sh
db-up:
	docker compose --env-file .env up -d postgres
db-down:
	docker compose --env-file .env down
serve:
	uv run uvicorn prawdowid.main:app --reload --port 8000
worker:
	@echo 'Worker not implemented in starter. Codex task P0-03.'; exit 1
test:
	uv run pytest -q
lint:
	uv run ruff check .
smoke:
	bash scripts/smoke.sh
