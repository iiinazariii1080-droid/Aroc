# Igus Service (Dryve D1) — Dockerized

A small FastAPI service wrapping the `dryve_d1` driver (Modbus/TCP → CiA 402).

## Quick start

## Development & tests

Install test dependencies:
```bash
python -m pip install -r requirements-dev.txt
```

Run tests:
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_asyncio.plugin tests -m "not simulator"
```

Run full local CI parity checks:
```bash
bash run_ci_local.sh
```

CI quality gate:
- GitHub Actions workflow: `.github/workflows/ci.yml`
- Runs on push/pull request.
- Installs `requirements-dev.txt` and executes:
	- `python -m ruff check drivers/dryve_d1`
	- `python -m mypy --config-file mypy-driver.ini drivers/dryve_d1`
	- `python -m pytest -q -p pytest_asyncio.plugin tests -m "not simulator"`
	- `python -m ruff check main.py app tests`
	- `python -m mypy`

Local static checks:
```bash
python -m ruff check main.py app tests
python -m mypy
python -m ruff check drivers/dryve_d1
python -m mypy --config-file mypy-driver.ini drivers/dryve_d1
```

Contribution and PR governance:
- `CONTRIBUTING.md`
- `.github/pull_request_template.md`
- `REFACTOR_SUMMARY_2026_02.md` (dedup/dead-code cleanup summary)

### 1) Build
```bash
docker build -t igus_service:latest .
```

### 2) Configure environment
Copy and edit `.env.example` (recommended), or pass env vars directly.

Key variables:
- `IGUS_MOTOR_IP` / `DRYVE_HOST` — drive (or simulator) IP/hostname
- `IGUS_MOTOR_PORT` / `DRYVE_PORT` — Modbus/TCP port (**502** is the common default)
- `DRYVE_UNIT_ID` — Modbus unit id (**1..255**, per typical gateway docs)

**Simulator example (your endpoint):**
- host: `82.165.177.194`
- port: `501`

So:
```bash
export IGUS_MOTOR_IP=82.165.177.194
export IGUS_MOTOR_PORT=501
```

### 3) Run
```bash
docker compose up -d --build
```

### 4) Open Swagger
- http://localhost:8101/docs

## API

- Legacy endpoints: `/move`, `/reference`, `/fault_reset`, `/status`, ...
- Versioned API: `/drive/*` (+ SSE stream at `/drive/events`)

Legacy API deprecation protocol:
- Legacy endpoints return `Deprecation: true` and `Sunset` headers.
- Migration target is `/drive/*`.
- Deprecation behavior is configurable via:
	- `LEGACY_API_DEPRECATION` (default `true`)
	- `LEGACY_API_SUNSET` (HTTP-date)
	- `LEGACY_API_DOCS_LINK` (default `/docs`)
	- `LEGACY_API_PHASE` (`deprecated` | `sunset` | `removed`, default `deprecated`)
	- `LEGACY_API_SUCCESSOR_PATH` (default `/drive`)

Breaking-change protocol:
- `deprecated`: legacy endpoints work and emit deprecation headers.
- `sunset`: same runtime behavior as `deprecated`, but sunset date should be imminent.
- `removed`: legacy endpoints fail-closed with `410 Gone` and code `LEGACY_API_REMOVED`.
- Phase gauge in metrics: `igus_legacy_api_phase{phase=...}` (exactly one phase has value `1`).

Error codes registry:
- Canonical error codes are centralized in `app/error_codes.py`.
- HTTP error normalization uses registry defaults for 4xx/5xx.

## Notes

- The container listens on `0.0.0.0:8101`.
- For remote networks/VPNs, tune timeouts via `DRYVE_*` variables in `app/config.py`.
- Some simulators do not echo Modbus Transaction-Id; set `DRYVE_ALLOW_TID_MISMATCH=1` if needed.

## Observability

- Metrics endpoint: `GET /metrics` (Prometheus format)
- Readiness endpoint: `GET /ready` (returns `503` when disconnected or degraded)
- Key driver health metrics:
	- `igus_drive_connected` — 1 when driver connection is active
	- `igus_drive_last_telemetry_age_seconds` — age of last telemetry snapshot
	- `igus_drive_telemetry_stale` — 1 when telemetry is stale (based on poll interval)
	- `igus_drive_fault_active` — latest drive fault bit
	- `igus_drive_startup_error_present` — startup/connect error flag
	- `igus_drive_degraded` — aggregate degraded flag (0/1)
	- `igus_drive_health_score` — aggregate health score (0..100)
	- `igus_drive_telemetry_callback_errors_total` — callback processing failures
	- `igus_drive_operation_errors_total{operation,code,status}` — operation-level error counters
	- `igus_legacy_api_requests_total{path,phase}` — legacy endpoint usage by phase

	Prometheus alert rules example for legacy migration governance:
	- `monitoring/legacy-api-alert-rules.yml`
	- `monitoring/legacy-api-runbook.md`

Recommended alerting baseline:
- `igus_drive_connected == 0` for > 10s
- `igus_drive_telemetry_stale == 1` for > 10s
- `increase(igus_drive_operation_errors_total[5m]) > 0` for critical operations

### Trace IDs for diagnostics

- Command endpoints (`/drive/*` POST) return `meta.command_id` and `meta.request_id`.
- Legacy command endpoints (`/move`, `/reference`, `/fault_reset`) return `command_id` and `request_id` in response body.
- SSE stream (`/drive/events`) emits `type=command` events with `payload.command_id`, `payload.request_id`, `payload.op_id`, and `payload.operation`.
- `GET /drive/trace/latest` returns the last in-process command trace snapshot (`request_id`, `command_id`, `op_id`, `operation`).
- Driver operation logs include `op_id` for `move/jog/stop/quick_stop` transitions.
- Practical workflow for incidents:
	1. Start from API response (`request_id`, `command_id`).
	2. Find HTTP logs by `request_id`.
	3. Confirm async command completion path via SSE `type=command` payload.
	4. Follow driver logs by `op_id` to inspect state transitions and timeout context.


## Simulator notes
If you use the bundled Modbus TCP simulator, it responds with Unit ID 0 in MBAP. Set `DRYVE_UNIT_ID=0` (recommended) or leave any value and keep `DRYVE_ALLOW_UNIT_ID_WILDCARD=1`.
