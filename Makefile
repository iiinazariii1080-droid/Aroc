# ── Aroc Robot – project-wide Makefile ──────────────────────────────────────
SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON     ?= python3
PIP        ?= $(PYTHON) -m pip
PYTEST     ?= $(PYTHON) -m pytest
RUFF       ?= $(PYTHON) -m ruff
MYPY       ?= $(PYTHON) -m mypy
COVERAGE   ?= $(PYTHON) -m coverage

# All services with tests (order: critical → important → other)
SERVICES := xarm_service igus_service robot_service \
            mqtt_command_service nav2adapter api_gateway_service \
            frontend_service janus_camera_page

# ── Help ────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Install ─────────────────────────────────────────────────────────────────
.PHONY: install-dev
install-dev: ## Install dev/test dependencies
	$(PIP) install -r requirements-dev.txt

# ── Lint ────────────────────────────────────────────────────────────────────
.PHONY: lint
lint: ## Run ruff check on all services
	@for svc in $(SERVICES); do \
	  echo "── ruff: $$svc ──"; \
	  $(RUFF) check $$svc || exit 1; \
	done
	@echo "✓ ruff passed"

.PHONY: lint-fix
lint-fix: ## Run ruff with --fix
	@for svc in $(SERVICES); do \
	  $(RUFF) check --fix $$svc; \
	done

# ── Type check ──────────────────────────────────────────────────────────────
.PHONY: typecheck
typecheck: ## Run mypy on all services
	@for svc in $(SERVICES); do \
	  echo "── mypy: $$svc ──"; \
	  $(MYPY) --ignore-missing-imports $$svc/app/ 2>/dev/null || true; \
	done

# ── Tests ───────────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run unit tests for all services (no hardware)
	@fail=0; \
	for svc in $(SERVICES); do \
	  if [ -d "$$svc/tests" ]; then \
	    echo ""; \
	    echo "══════════════════════════════════════════════════════════════"; \
	    echo "  Testing: $$svc"; \
	    echo "══════════════════════════════════════════════════════════════"; \
	    PYTHONPATH="$$svc:$$PWD:$$PYTHONPATH" \
	    $(PYTEST) "$$svc/tests" \
	      -m "not hardware and not simulator" \
	      --timeout=30 \
	      -q --tb=short \
	      2>&1 || fail=1; \
	  fi; \
	done; \
	if [ $$fail -eq 1 ]; then echo "✗ Some tests failed"; exit 1; fi; \
	echo ""; echo "✓ All tests passed"

.PHONY: test-service
test-service: ## Run tests for a single service: make test-service SVC=xarm_service
	@if [ -z "$(SVC)" ]; then echo "Usage: make test-service SVC=<name>"; exit 1; fi
	PYTHONPATH="$(SVC):$$PWD:$$PYTHONPATH" \
	$(PYTEST) "$(SVC)/tests" \
	  -m "not hardware and not simulator" \
	  --timeout=30 -v --tb=short

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@for svc in $(SERVICES); do \
	  if [ -d "$$svc/tests" ]; then \
	    echo "── coverage: $$svc ──"; \
	    PYTHONPATH="$$svc:$$PWD:$$PYTHONPATH" \
	    $(PYTEST) "$$svc/tests" \
	      -m "not hardware and not simulator" \
	      --cov="$$svc/app" \
	      --cov-report=term-missing:skip-covered \
	      --timeout=30 -q --tb=short \
	      2>&1 || true; \
	    echo ""; \
	  fi; \
	done

# ── CI-like full pipeline ──────────────────────────────────────────────────
.PHONY: ci
ci: lint test ## Run lint + test (CI pipeline locally)
	@echo ""; echo "✓ CI passed"

# ── Clean ───────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove test artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name .coverage -delete 2>/dev/null || true
	find . -name coverage.xml -delete 2>/dev/null || true

# ── Docker ──────────────────────────────────────────────────────────────────
.PHONY: up down up-prod env-check

up: ## Start all services (docker compose up -d --build)
	docker compose up -d --build

down: ## Stop all services
	docker compose down

up-prod: ## Start with production hardening overlay
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

env-check: ## Validate that .env exists and required vars are set
	@if [ ! -f .env ]; then \
	  echo "✗ .env not found — run: cp .env.example .env"; exit 1; \
	fi; \
	missing=0; \
	for var in HOST_LAN_IP XARM_HARDWARE_IP IGUS_MOTOR_IP DEPTH_CAMERA_IP SYMOVO_CAR_IP; do \
	  if ! grep -qE "^$$var=" .env; then \
	    echo "✗ Missing $$var in .env"; missing=1; \
	  fi; \
	done; \
	if [ $$missing -eq 1 ]; then echo ""; echo "See .env.example for reference"; exit 1; fi; \
	echo "✓ .env looks good"
