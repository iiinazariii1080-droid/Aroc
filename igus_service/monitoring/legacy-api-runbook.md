# Legacy API Migration Runbook

This runbook defines how to move `LEGACY_API_PHASE` safely:

- `deprecated` -> `sunset` -> `removed`

## Preconditions

- Metrics are scraped from `/metrics`.
- Alerts from `monitoring/legacy-api-alert-rules.yml` are enabled.
- Successor clients are migrated to `/drive/*` endpoints.

## SLO-based thresholds

Use these baseline thresholds before phase transitions:

- Move to `sunset` when:
  - `sum(rate(igus_legacy_api_requests_total{phase="deprecated"}[7d])) < 0.01`
  - No critical clients depend on legacy paths.
- Move to `removed` when (for at least 14 days):
  - `sum(rate(igus_legacy_api_requests_total{phase=~"deprecated|sunset"}[7d])) == 0`
  - No migration-blocking incidents in the previous 7 days.

## Phase execution checklist

### 1) Switch to `sunset`

- Set env:
  - `LEGACY_API_PHASE=sunset`
  - `LEGACY_API_SUNSET=<target RFC1123 date>`
- Deploy.
- Verify:
  - Legacy responses include `Deprecation`, `Sunset`, `X-API-Phase: sunset`.
  - `igus_legacy_api_phase{phase="sunset"} == 1`.

### 2) Switch to `removed`

- Set env:
  - `LEGACY_API_PHASE=removed`
- Deploy.
- Verify:
  - Legacy responses return `410` with code `LEGACY_API_REMOVED`.
  - Successor endpoints remain healthy.
  - `igus_legacy_api_phase{phase="removed"} == 1`.

## Rollback

If critical traffic still hits legacy routes after removal:

- Immediate rollback env:
  - `LEGACY_API_PHASE=sunset`
- Redeploy and notify client owners.
- Keep migration alert active until traffic drops again.

## Evidence to record

For each phase change, record:

- Deployment ID and timestamp.
- 24h snapshot of:
  - `igus_legacy_api_requests_total{path,phase}`
  - `igus_legacy_api_phase{phase}`
- Incident/no-incident decision notes.
