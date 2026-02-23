# Runtime Log Analysis Report

Generated: 2026-02-13
Source bundle: `research_logs/20260213_163446`

## Executive Summary
- Gateway container is healthy and stable at process level.
- OpenAPI refresh loop is healthy: no refresh failures observed.
- Main traffic is successful (`200` dominates).
- Two functional anomalies exist and require follow-up:
  1. repeated upstream `500` from `depth_camera` endpoint,
  2. repeated auth refresh warnings (`404` on auth endpoint).

## Key Metrics
- HTTP status histogram from full logs:
  - `200`: 2311
  - `500`: 13
- `depth_camera/depth` upstream `500` count: **13**
- auth refresh warning (`404`) count: **3**
- circuit-open events: **0**

## Resource Snapshot
- CPU: ~`1.26%`
- RAM: ~`68.25 MiB`
- PIDs: `3`
- Time-series (60s): CPU remained low (`0.19%`..`1.74%`), RAM stable (`68.25`..`68.5 MiB`).

## Health Snapshot
- `/healthz`: `status=ok`, `http_client_ready=true`
- OpenAPI refresh metrics: attempts/successes increased, failures remained `0`.

## Preliminary Diagnosis
1. `depth_camera` errors are upstream-origin failures (gateway logs status `500` returned by upstream route).
2. Auth refresh `404` indicates incorrect auth endpoint path/config or unavailable auth route on target hub.
3. No evidence of gateway-level resource leak, overload, or circuit-breaker thrash in this window.

## Next Steps
1. Validate `depth_camera` upstream logs for the same timestamps around 15:27 and 15:32.
2. Verify hub auth config (`base_url` / `auth_url`) and expected route for robot token endpoint.
3. Keep periodic bundle collection to confirm whether anomalies are transient or persistent.
