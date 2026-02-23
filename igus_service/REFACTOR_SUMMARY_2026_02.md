# Refactor Summary (2026-02)

This document captures the refactor pass focused on deduplication and dead code removal in `igus_service`, while preserving API/runtime behavior.

## Scope

- Preserve legacy and v1 endpoint contracts.
- Reduce duplicated command handling and trace publication logic.
- Remove dead symbols/models/helpers.
- Keep runtime behavior and diagnostics compatibility.

## What changed

### 1) Shared command trace/event publication

- Added shared helper: `app/command_trace.py`
  - Publishes `EventType.COMMAND` payload.
  - Updates `app.state.latest_command_trace`.
  - Handles publish failures as best-effort logging.
- Replaced duplicated implementations in:
  - `app/api_routes.py`
  - `app/routes.py`

### 2) Command endpoint deduplication

- `app/api_routes.py`
  - Introduced common command executor for all command POST endpoints.
  - Unified generation/flow of `command_id` + `op_id` + trace publication.
- `app/routes.py` (legacy)
  - Introduced common legacy command executor.
  - Preserved response model (`ActionResponse`) and fields (`success`, `error`, `request_id`, `command_id`).

### 3) ServiceError HTTP mapping deduplication

- Added shared mapper: `app/service_error_http.py`
  - Maps `ServiceError` to `HTTPException`.
  - Optionally records operation-level metrics when `request` and `operation` are provided.
- Integrated into both routers (`app/api_routes.py`, `app/routes.py`).

### 4) Use-cases snapshot deduplication and typing

- `app/application/use_cases.py`
  - Added shared snapshot resolution helpers for status/telemetry paths.
  - Introduced typed container (`_ResolvedSnapshot`) instead of string-key dict access.
  - Added micro-helpers for `op_id` generation and optional float conversion.

### 5) Version centralization

- Added `app/version.py` with `SERVER_VERSION`.
- Wired into:
  - `main.py` (FastAPI app version and `/info` response)
  - `app/api_models.py` (`Meta.server_version` default)

### 6) Dead code and unused symbols removed

- Removed dead/unused symbols and imports from:
  - `app/http_errors.py` (unused `raise_http_error`)
  - `app/api_models.py` (unused `TelemetrySample`, `CommandResponse`)
  - `app/decorator.py` (unused import/constant)
  - Router modules: unused imports cleaned.

### 7) Documentation alignment

- Updated route references in `README.md` to actual mounted API routes (`/drive/*`, `/drive/events`).
- Trace/runbook docs kept aligned with current trace model:
  - `request_id`, `command_id`, `op_id`
  - `/drive/trace/latest`

## Compatibility and invariants preserved

- Legacy endpoints remain available and behavior-compatible.
- v1 endpoints keep `ApiEnvelope` shape and command metadata behavior.
- SSE command events keep payload compatibility and include trace IDs.
- Drive motion/state flows are unchanged by this refactor pass.

## Validation results

Validated with CI-parity local gate (`run_ci_local.sh`):

- `ruff` checks: pass
- `mypy` checks: pass
- `pytest` suite: pass (`35 passed`, known non-blocking warning)

## Deferred (intentionally not changed in this pass)

- `StateCache` private fallback compatibility path retained (risk-managed).
- Legacy API removal/migration behavior not altered.
- No route prefix migration (`/api/v1`) performed; docs aligned to current runtime.
