# Test Plan — V2

Дата: 2026-01-28

## 1) Unit tests
- ReadinessGate:
  - connected/faulted/motion_enabled/busy → ready matrix
- CommandService policies:
  - REJECT_IF_BUSY / ALLOW_PREEMPT / QUEUE
- Idempotency:
  - повторный command_id возвращает cached result
- Error mapping:
  - SDK error_code → доменные исключения/HTTP

## 2) Integration tests (mock SDK)
- Actor executes commands sequentially
- Concurrent HTTP requests:
  - assert no parallel SDK calls (single-writer)
- Cancel:
  - cancel running job returns canceled (best effort)
- Reconnect:
  - simulate disconnect → actor reconnects → ready becomes true

## 3) Hardware smoke (на реальном роботе)
- Pre-check:
  - robot in safe area, speed limits, e-stop accessible
- Scenario S1:
  - connect → readiness true (без авто recover)
- Scenario S2:
  - enable_motion → move small delta → stop
- Scenario S3:
  - fault → readiness false → recover → readiness true
- Scenario S4:
  - busy policy: send two move commands (expect reject/preempt according to config)

## 4) Non-functional
- Latency budget:
  - status endpoints быстрые (не блокируются длительными командами)
- Soak test:
  - N минут команд + periodic status without memory leak


## Workspace Envelope Tests (V3)
- Unit: проверка пересчёта WS→base, margin, capsule-in-box.
- Integration: TrajectoryValidator отклоняет команды, выходящие за WS.
- Hardware smoke: медленные перемещения к 6 граням WS (с запасом), проверка reduced mode и runtime guard.
