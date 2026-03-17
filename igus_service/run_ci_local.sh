#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
	python3 -m venv "${VENV_DIR}"
fi

PY="${VENV_DIR}/bin/python"

"${PY}" -m pip install --upgrade pip
"${PY}" -m pip install -r "${ROOT_DIR}/requirements-dev.txt"

"${PY}" -m ruff check drivers/dryve_d1
"${PY}" -m mypy drivers/dryve_d1

"${PY}" -m ruff check main.py app tests
"${PY}" -m mypy main.py app

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${PY}" -m pytest -q -p pytest_asyncio.plugin -p pytest_cov.plugin \
  --cov=app --cov-report=term-missing:skip-covered --cov-fail-under=60 \
  tests -m "not simulator"
