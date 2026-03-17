# ADR 0003: Deterministic backoff by default

## Context

Exponential backoff with jitter (e.g. `Math.random()`) improves resilience in production by spreading retries, but it makes core logic non-deterministic and tests harder to reproduce. The player core should stay pure and testable without sacrificing the option to use jitter when configured.

## Decision

- **backoffJitterRatio** defaults to **0** in both `core/backoff.js` and in config. When jitter ratio is 0 or undefined, the backoff module does not call `Math.random()`: jitter amplitude is 0 and the computed delay is deterministic for the same (attempt, cfg).
- Config (e.g. from dataset) may set a positive `backoffJitterRatio` (e.g. 0.25) for production; then the core does use `Math.random()` for that process. By default, tests and any environment that do not set jitter get deterministic delays.

## Consequences

- Core remains deterministic and pure when jitter is off; no direct `Math.random()` in core for the default configuration.
- Unit tests can assert exact backoff values (e.g. attempt 1 → 500 ms, attempt 2 → 900 ms) and that repeated calls with the same inputs yield the same output.
- Production can enable jitter without code changes, via config only.
