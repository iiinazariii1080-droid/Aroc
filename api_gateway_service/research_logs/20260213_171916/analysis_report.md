# Runtime Investigation Report

Generated: 2026-02-13
Bundle: `research_logs/20260213_171916`

## Summary
- Runtime in the last 30 minutes is stable.
- No proxy/auth/circuit/openapi anomalies were detected in this window.
- Critical finding: **deployment drift** — running container code is older than workspace code.

## Quantitative Findings (last 30m)
- HTTP status histogram: `{200: 3380}`
- signal lines (`error|warning|failed|timeout|exception`): `0`
- `depth_camera` 5xx count: `0`
- auth refresh 404 count: `0`
- circuit open events: `0`

## Health / Resource Snapshot
- `/healthz` status: `ok`
- OpenAPI refresh: attempts `32`, successes `32`, failures `0`
- Docker resource snapshot:
  - CPU ~`1.32%`
  - RAM ~`67.43 MiB`
  - PIDs `3`

## Bug Found: Deployment Drift (High)
Evidence:
- Host code includes `service_error_rates` in `app/routers/health.py`.
- Container code does **not** include this field.
- Runtime `/healthz` payload lacks `service_error_rates` key.

This means the running container isn't built from the latest workspace state.

## Recommended Immediate Action
1. Rebuild and restart container from current workspace (`docker compose up -d --build`).
2. Verify `/healthz` now includes `service_error_rates`.
3. Re-run this same investigation bundle after redeploy to confirm metrics are live.
