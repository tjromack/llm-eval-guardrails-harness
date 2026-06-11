# LLM Evaluation & Guardrails Harness — task runner.
# Targets are intentionally thin wrappers over module entrypoints so the same
# commands work in a demo and in CI. See CLAUDE.md for the contract.

# Cross-platform venv python path (Windows vs POSIX).
ifeq ($(OS),Windows_NT)
	VENV_PY := .venv/Scripts/python.exe
else
	VENV_PY := .venv/bin/python
endif
# Fall back to system python if the venv hasn't been created yet.
PY := $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: install seed eval-run selfcheck run test reset fmt help

help:
	@echo "install    - create .venv and install requirements"
	@echo "seed       - load the sample suite (RAG copilot + guardrail cases)   [Phase 1+]"
	@echo "eval-run   - run the configured suite against the target              [Phase 3+]"
	@echo "selfcheck  - validate the harness: judge calibration + fixtures       [Phase 7]"
	@echo "run        - start the FastAPI dashboard (uvicorn --reload)"
	@echo "test       - run pytest"
	@echo "reset      - clear runs + re-seed for a clean demo                    [Phase 6+]"
	@echo "fmt        - format with ruff"

install:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

test:
	$(PY) -m pytest -q

fmt:
	$(PY) -m ruff format app tests
	$(PY) -m ruff check --fix app tests

# --- Phases below wire up as their modules land; defined now so the demo path is stable. ---
seed:
	$(PY) -m app.seed

eval-run:
	$(PY) -m app.runner

selfcheck:
	$(PY) -m app.selfcheck

reset:
	$(PY) -m app.seed --reset
