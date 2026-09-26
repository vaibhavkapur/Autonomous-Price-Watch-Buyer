PY ?= .venv/bin/python
export PYTHONPATH := packages:apps

.PHONY: venv test test-report dev api worker merchant-a merchant-b web migration compose-up compose-down demo

venv:
	python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q fastapi uvicorn "sqlalchemy>=2" "pydantic>=2" httpx joserfc cryptography pyyaml pytest pytest-asyncio

test:
	$(PY) -m pytest -q

test-report:
	$(PY) scripts/test_report.py

dev:            ## all-in-one server (API + merchants + worker, SQLite, simulated clock) on :8000
	$(PY) apps/dev_server.py

api:
	$(PY) -m uvicorn --factory api.main:create_app --port 8000 --reload

worker:
	$(PY) apps/worker/main.py

merchant-a:
	$(PY) apps/merchant_a/main.py

merchant-b:
	$(PY) apps/merchant_b/main.py

web:
	cd apps/web && npm install && npm run dev

migration:
	$(PY) scripts/generate_migration.py

demo:           ## run demos A–D against the in-process harness and print the decision history
	$(PY) scripts/run_demo.py

compose-up:
	docker compose up --build

compose-down:
	docker compose down -v
