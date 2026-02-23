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
"${PY}" -m mypy --config-file mypy-driver.ini drivers/dryve_d1

"${PY}" -m ruff check main.py app tests
"${PY}" -m mypy

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${PY}" -m pytest -q -p pytest_asyncio.plugin -p pytest_cov.plugin tests -m "not simulator"
