# Security & Safety — V2

Дата: 2026-01-28

## 1) Safety (robotics)
- Любые действия, которые могут привести к движению:
  - выполняются только через `RobotActor`
  - требуют `motion_enabled == true`
- `connect()` НЕ должен:
  - auto clean_error/clean_warn
  - auto motion_enable(True)
- Recovery — явная операция (audit log + reason)

## 2) Teleop / joystick
- Deadman switch: отсутствие heartbeat → STOP
- Ограничение скорости/ускорения в конфиге
- Reject policy при faulted/unknown mode

## 3) Security (container)
- Не логировать IP/токены/ключи если есть
- Ограничить доступ к endpoints (auth/allowlist) при эксплуатации вне закрытой сети
- Настроить timeouts и rate limit (защита от flood)

## 4) Operational safeguards
- graceful shutdown: actor stops, callbacks released, SDK closed
- backoff при reconnect (не DDOS сеть)


## 5) Workspace Safety Envelope
См. `WORKSPACE_SAFETY_ENVELOPE_V3.md` — reduced mode boundary + software envelope (локти/звенья внутри зоны) + runtime guard.
