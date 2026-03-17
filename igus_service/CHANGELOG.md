# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-16

### Added

- FastAPI microservice for Igus Dryve D1 motor control via Modbus/TCP
- CiA 402 state machine implementation (DryveD1 driver)
- API v1 endpoints: move_to_position, jog, reference, fault_reset, stop, quick_stop
- Legacy API with deprecation protocol (phase-based lifecycle)
- X-API-Key authentication with timing-safe HMAC comparison
- Centralized error registry with 18 canonical error codes
- Health scoring algorithm with configurable weights
- SSE event bus for real-time telemetry streaming
- Prometheus metrics export (/metrics endpoint)
- Request tracing (request_id, command_id, op_id)
- Modbus TCP simulator for development and testing
- Docker support with non-root user and health checks
- CI pipeline: ruff, mypy, bandit, pip-audit, pytest
- Comprehensive test suite (24 test files)
- Static HTML control panel
- Monitoring resources (Prometheus alert rules, incident runbook)
