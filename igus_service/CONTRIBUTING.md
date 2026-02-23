# Contributing Guide

This repository uses strict quality and release controls.

## Branch protection (recommended)

Configure branch protection for the default branch with:

- Require pull request before merging.
- Require at least 1 approval.
- Require status checks to pass:
  - `static`
  - `test`
- Require branches to be up to date before merging.
- Restrict force-push and branch deletion.

## Pull request requirements

Every PR must include:

- Problem statement and root cause.
- Scope and architecture impact.
- Backward compatibility impact (or explicit "none").
- Rollback plan.
- Test evidence.

## Local quality gates

Run before opening PR:

```bash
python -m ruff check main.py app tests
python -m mypy
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_asyncio.plugin tests -m "not simulator"
```

## API compatibility rules

- New endpoints should prefer versioned API paths.
- Legacy endpoints follow lifecycle policy (`deprecated` -> `sunset` -> `removed`).
- Breaking changes must update:
  - `README.md`
  - `monitoring/legacy-api-runbook.md`
  - deprecation env/config values

## Commit and review expectations

- Keep changes PR-sized and focused.
- No unrelated refactors in the same PR.
- Add or update tests for every behavior change.
- If adding new error paths, register codes in `app/error_codes.py`.
