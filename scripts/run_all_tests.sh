#!/usr/bin/env bash
# ── Run all tests across the project ────────────────────────────────────────
# Usage: ./scripts/run_all_tests.sh [--cov] [--verbose]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COV_FLAG=""
VERBOSE_FLAG="-q"

for arg in "$@"; do
  case "$arg" in
    --cov) COV_FLAG="--cov" ;;
    --verbose|-v) VERBOSE_FLAG="-v" ;;
  esac
done

SERVICES=(
  xarm_service
  igus_service
  robot_service
  mqtt_command_service
  nav2adapter
  api_gateway_service
  frontend_service
  janus_camera_page
)

# Env vars to disable hardware in tests
export XARM_IP="127.0.0.1"
export WS_CHECK_DISABLED="1"
export DRYVE_HOST="127.0.0.1"
export MQTT_BROKER="127.0.0.1"
export DEPTH_BASE_URL="http://127.0.0.1:9999"
export CONFIG_DB_PATH=":memory:"
export API_ENABLED="false"

TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SERVICES=()

for svc in "${SERVICES[@]}"; do
  if [ ! -d "$svc/tests" ]; then
    echo "⏭  $svc – no tests/ directory"
    continue
  fi

  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "  Testing: $svc"
  echo "══════════════════════════════════════════════════════════════"

  COV_ARGS=""
  if [ -n "$COV_FLAG" ]; then
    COV_ARGS="--cov=$svc/app --cov-report=term-missing:skip-covered"
  fi

  if PYTHONPATH="$svc:$ROOT_DIR:${PYTHONPATH:-}" \
     python3 -m pytest "$svc/tests" \
       -m "not hardware and not simulator" \
       --timeout=30 \
       $VERBOSE_FLAG --tb=short \
       $COV_ARGS; then
    TOTAL_PASS=$((TOTAL_PASS + 1))
  else
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    FAILED_SERVICES+=("$svc")
  fi
done

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Summary: $TOTAL_PASS passed, $TOTAL_FAIL failed"
if [ ${#FAILED_SERVICES[@]} -gt 0 ]; then
  echo "  Failed: ${FAILED_SERVICES[*]}"
fi
echo "════════════════════════════════════════════════════════════════"

exit "$TOTAL_FAIL"
