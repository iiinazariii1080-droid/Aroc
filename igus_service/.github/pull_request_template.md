## Summary

- What changed?
- Why was it needed?

## Root Cause

- Underlying issue being solved.

## Architecture Impact

- Layers/components affected.
- Dependency/boundary changes.

## Backward Compatibility

- [ ] No breaking change
- [ ] Breaking change (describe migration path)

## Legacy API Lifecycle Impact

- [ ] Not affected
- [ ] Affected (describe `deprecated|sunset|removed` implications)

## Test Evidence

Paste command output snippets:

```bash
python -m ruff check main.py app tests
python -m mypy
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_asyncio.plugin tests -m "not simulator"
```

## Rollback Plan

- How to revert safely if incident occurs.

## Operational Notes

- Metrics/alerts changed? If yes, list dashboards/rules touched.
