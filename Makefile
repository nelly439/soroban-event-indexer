# Common tasks. Run `make help` for the list.
PY      := backend/.venv/bin/python
BIN     := backend/.venv/bin
COMPOSE := docker compose

.PHONY: help install test test-backend test-frontend e2e lint typecheck docs-check check \
        build demo up up-postgres up-demo down clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install:  ## Create the backend venv and install frontend deps
	test -d backend/.venv || python3 -m venv backend/.venv
	$(BIN)/pip install -r backend/requirements-dev.txt
	$(BIN)/pip install --no-deps -e backend
	cd frontend && npm ci

test: test-backend test-frontend  ## Unit tests (backend + frontend)

test-backend:  ## Backend unit tests with coverage
	cd backend && .venv/bin/pytest --cov

test-frontend:  ## Frontend unit tests
	cd frontend && npm test

e2e:  ## End-to-end tests (real processes against the fake RPC)
	$(BIN)/pytest tests-e2e -p no:cacheprovider

lint:  ## Ruff lint + format check
	cd backend && .venv/bin/ruff check . ../tests-e2e ../scripts && .venv/bin/ruff format --check . ../tests-e2e ../scripts

typecheck:  ## mypy (strict) + tsc
	cd backend && .venv/bin/mypy soroban_indexer
	cd frontend && npx tsc -b

docs-check:  ## Verify docs/.env.example match the code's ENV_VARS
	$(PY) scripts/docs_check.py

check: lint typecheck docs-check test e2e  ## Everything CI runs

build:  ## Build the frontend bundle
	cd frontend && npm run build

demo:  ## Run the stack locally (no Docker) against the fake RPC
	scripts/demo.sh

up:  ## Docker: SQLite + testnet
	$(COMPOSE) up -d --build

up-postgres:  ## Docker: PostgreSQL + testnet
	$(COMPOSE) -f docker-compose.yml -f docker-compose.postgres.yml up -d --build

up-demo:  ## Docker: offline demo with the fake RPC
	$(COMPOSE) -f docker-compose.yml -f docker-compose.demo.yml up -d --build

down:  ## Docker: stop every variant (keeps volumes)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.postgres.yml -f docker-compose.demo.yml down

clean:  ## Remove local build/test artefacts
	rm -rf frontend/dist .demo-work .e2e-work backend/.coverage backend/.pytest_cache
