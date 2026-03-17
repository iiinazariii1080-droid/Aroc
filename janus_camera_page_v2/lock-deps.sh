#!/usr/bin/env bash
# Generate a pinned requirements.lock from requirements.txt.
#
# Run this in CI or before deploying to production to ensure
# reproducible installs on the rover.  The lock file pins exact
# versions of every transitive dependency.
#
# Usage:
#   ./lock-deps.sh                    # uses system python3
#   PYTHON=python3.11 ./lock-deps.sh  # explicit interpreter

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv-lock"

echo "Creating temporary venv for dependency resolution..."
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip freeze > requirements.lock

deactivate
rm -rf "$VENV"

echo "requirements.lock generated ($(wc -l < requirements.lock) packages pinned)"
