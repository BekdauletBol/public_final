# public.safe — developer entry points (macOS first; Linux works for tests).
# Run `make help` for a summary. Every target is documented in README.md.

SHELL := /bin/bash
.ONESHELL:
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV := $(ROOT)/.venv
PY := $(VENV)/bin/python
BIN := $(VENV)/bin
UV ?= uv
PYTHON_VERSION := $(shell cat $(ROOT)/.python-version)
NPM ?= npm
NVM_SH := $(HOME)/.nvm/nvm.sh
CONFIG ?= configs/e1_mot17.yaml

# Pick the Node version from .nvmrc when nvm is installed; otherwise use PATH.
NODE_ENV = if [ -s "$(NVM_SH)" ]; then source "$(NVM_SH)" && nvm use --silent >/dev/null 2>&1 || true; fi

.PHONY: help setup lock certs dev run build test test-browser test-load test-model eval lint format clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

setup: ## create .venv (Python $(PYTHON_VERSION)), install pinned deps, npm ci, copy .env.example
	$(UV) venv $(VENV) --python $(PYTHON_VERSION)
	$(UV) pip install --python $(PY) --no-deps -r backend/requirements.txt
	$(UV) pip install --python $(PY) --no-deps -e backend
	cd frontend && $(NODE_ENV); $(NPM) ci
	mkdir -p data certs
	[ -f .env ] || cp .env.example .env
	@echo
	@echo "Setup complete. Next:"
	@echo "  make certs                          # HTTPS certificate (needs mkcert)"
	@echo "  $(BIN)/publicsafe admin set-password  # from M1"
	@echo "  make dev   or   make run"

lock: ## re-resolve backend/pyproject.toml and rewrite backend/requirements.txt (pinned freeze)
	$(UV) pip install --python $(PY) --override backend/overrides.txt -e "backend[dev]"
	$(UV) pip freeze --python $(PY) | grep -v '^-e ' | grep -vi '^publicsafe[ =@]' > backend/requirements.txt
	@echo "wrote backend/requirements.txt ($$(wc -l < backend/requirements.txt) packages)"

certs: ## issue certs/dev.pem + dev-key.pem with mkcert for localhost, LAN IPs and <host>.local
	$(BIN)/publicsafe certs

dev: ## backend on https://0.0.0.0:8443 (reload) + Vite on https://0.0.0.0:5173 (proxy /api, /ws)
	trap 'kill 0' INT TERM EXIT
	$(BIN)/publicsafe run --reload &
	cd frontend && $(NODE_ENV); $(NPM) run dev
	wait

build: ## build the frontend into frontend/dist
	cd frontend && $(NODE_ENV); $(NPM) run build

run: build ## serve everything from https://0.0.0.0:8443 (demo / Definition of Done)
	$(BIN)/publicsafe run

test: ## unit + integration + security + cv tests (no network, no GPU, FakeDetector)
	cd backend && $(PY) -m pytest -m "not model and not load and not browser" -q

test-model: ## real YOLOv8n on the golden image (downloads weights once)
	cd backend && $(PY) -m pytest -m model -q

test-browser: ## Playwright (Chromium) end-to-end tests
	@if [ -f frontend/playwright.config.ts ]; then cd frontend && $(NODE_ENV); npx playwright test; \
	else echo "test-browser: the Playwright suite arrives in M3"; fi

test-load: ## load test against a TEST_MODE server; asserts fairness and memory bound
	@if [ -f backend/tests/load/run_loadtest.py ]; then $(PY) backend/tests/load/run_loadtest.py; \
	else echo "test-load: the load test arrives in M3"; fi

eval: ## run the evaluation harness: make eval CONFIG=configs/e1_mot17.yaml
	@if [ -f "$(CONFIG)" ]; then $(BIN)/publicsafe eval run --config "$(CONFIG)"; \
	else echo "eval: $(CONFIG) not found (the harness arrives in M5)"; fi

lint: ## ruff + black --check + mypy (strict on core) + eslint + prettier --check + tsc
	cd backend && $(BIN)/ruff check . && $(BIN)/black --check . && $(BIN)/mypy
	cd frontend && $(NODE_ENV); $(NPM) run lint && $(NPM) run typecheck

format: ## auto-format Python and TypeScript
	cd backend && $(BIN)/ruff check --fix . && $(BIN)/black .
	cd frontend && $(NODE_ENV); $(NPM) run format

clean: ## remove build outputs and caches (keeps .venv, data/, certs/)
	rm -rf frontend/dist backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache backend/htmlcov
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
