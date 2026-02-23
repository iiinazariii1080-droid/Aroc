# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Security headers middleware** — `X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy` on all responses
- **CORS middleware** — deny-all by default (API-only service)
- **GitHub Actions CI** — lint, type check, test (Python 3.11 & 3.12), security scan, Docker build
- **Pre-commit hooks** — trailing whitespace, ruff lint/format, mypy
- **Docker hardening** — `read_only`, `no-new-privileges`, `cap_drop: ALL`, `tmpfs`
- `types-requests` stub package for mypy
- Full type annotations across all core modules

### Changed
- **Test coverage** increased to 82% (715 tests)
- **Ruff linter** now enforces 0 errors across entire codebase
- **Mypy** strict mode passes on all 12 core source files
- Replaced deprecated `typing.Dict`/`typing.List` with native generics
- Replaced deprecated `datetime.utcnow()` with `datetime.now(UTC)`
- Sorted and cleaned all imports with `isort` via ruff

### Fixed
- Removed unused imports in `config.py` and `telemetry.py`
- Fixed bare `except:` in `automated_manual_tests.py`
- Fixed type-unsafe code patterns in `bridge.py` (union-attr narrowing)
- Fixed `None / float` operator error in `telemetry.py`
- Removed dead test (`test_import_failure`) testing removed functionality
- Fixed `BrokerConfigResponse` construction to use `model_validate`

### Security
- Added security headers to all HTTP responses
- Added CORS deny-all policy
- Docker: filesystem now read-only, capabilities dropped, no-new-privileges
- TLS certificate validation enforced by default (`tls_insecure=False`)

## [1.0.0] - 2024-01-01

### Added
- Initial release with MQTT-to-HTTP bridge
- REST API for broker configuration
- TLS/mTLS support
- API key authentication with RBAC
- Telemetry publishing (system, connection, navigation status)
- Task watcher for long-running operations
- Docker support with health checks
