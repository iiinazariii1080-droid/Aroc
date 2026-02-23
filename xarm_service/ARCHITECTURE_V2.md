# Архитектура xArm Service — V2 (robotics‑grade)

Дата: 2026-01-28

## 1. Цели и инварианты

### 1.1. Цели
- **Детерминированное управление**: отсутствие гонок и параллельных команд к SDK.
- **Безопасность**: разделить `connect` / `recover` / `enable_motion`, запретить “авто‑сброс ошибок” без явного действия.
- **Production‑операционность**: health/readiness, метрики, трассировка команд.
- **Эволюция API без поломок**: сохранить текущие HTTP endpoints (v1), добавить правильную job/action модель (v2) без breaking changes.

### 1.2. Архитектурные инварианты (MUST)
1. **Single Owner of SDK**: объект `XArmAPI` создаётся и используется только внутри `RobotActor`.
2. **Single‑Writer**: все операции, которые могут изменять состояние робота (движение, reset, enable, gripper) выполняются строго последовательно через очередь команд.
3. **Callbacks ≠ Business logic**: callbacks только обновляют состояние/события (StateStore/EventBus), не запускают движение.
4. **Explicit recovery**: `clean_error/clean_warn`, `motion_enable(True)` выполняются только по явной команде (recover/enable), не в `connect()`.
5. **Idempotency**: внешние команды имеют `command_id` и повторение не должно дублировать движение.


## 1.3. Safety Envelope (Workspace) — MUST
- Система обязана гарантировать, что **TCP и все звенья** манипулятора остаются внутри ограниченной зоны.
- Включить 2 уровня защиты:
  1) hardware reduced/boundary mode (контроллер)
  2) software envelope check (FK всех звеньев + валидация траектории)
- См. `WORKSPACE_SAFETY_ENVELOPE_V3.md`.

\1 Слои системы

### 2.1. Слои (Responsibilities)
- **API Layer (FastAPI)**: HTTP контракты, auth, валидация входа, корреляция request_id.
- **Application Layer (UseCases / CommandService)**: политика выполнения (busy/preempt), идемпотентность, формирование `Command`.
- **Robot Execution Layer (RobotActor)**: очередь, таймауты, отмена, единственный владелец SDK.
- **Device SDK Layer (XArm SDK)**: реальные вызовы `XArmAPI` (sync, в threadpool).
- **State/Telemetry Layer**: `StateStore`, readiness gate, events, metrics.

## 3. Компоненты

### 3.1. RobotActor
Фоновая корутина/таск, которая:
- поднимает соединение (open/close) и держит `XArmAPI`;
- подписывает callbacks один раз на connect и снимает на shutdown;
- принимает `Command` из `asyncio.Queue`;
- выполняет команду (в threadpool) и публикует `CommandResult`;
- поддерживает `cancel/preempt` (best‑effort) и дедлайны.

### 3.2. CommandService (Application Layer)
Решает “можно ли сейчас выполнять”:
- проверяет `ReadinessGate` (`connected`, `faulted`, `motion_enabled`, `busy`);
- применяет policy: `REJECT_IF_BUSY`, `ALLOW_PREEMPT`, `QUEUE`;
- применяет idempotency по `command_id` (например, LRU cache результатов).

### 3.3. StateStore + ReadinessGate
- `ConnectionState`: connected, last_seen, reconnect_count
- `RobotState`: error_code, warn_code, mode, is_moving, joint/pose, gripper
- `ExecutionState`: active_command_id, busy, last_result
- `Ready = connected && !faulted && motion_enabled && !busy`

## 4. Конкурентная модель

### 4.1. Правило
- **Никаких прямых вызовов SDK из роутеров/операций.**
- Все mutating операции → `CommandService` → `RobotActor`.

### 4.2. Threading/async
- SDK синхронный → выполнять через `anyio.to_thread.run_sync` или `starlette.concurrency.run_in_threadpool`.
- Снаружи всё async.
- Внутри RobotActor: один consumer loop.

## 5. Fault recovery и safety

### 5.1. Разделение операций
- `connect()` — только транспорт + callbacks + state reporting
- `recover_faults()` — clean_error/clean_warn, сброс fault flags
- `enable_motion()` — motion_enable(True) (опционально с interlocks)
- `disable_motion()` — motion_enable(False), стоп

### 5.2. Deadman / Watchdog (для teleop)
Для джойстика: если не было heartbeats N мс → стоп/disable teleop.

## 6. API стратегия (v1 + v2)

### 6.1. V1 façade (без ломки клиентов)
Оставляем текущие endpoints, но внутри они формируют `Command` и ждут результата (или возвращают job id в будущем).

### 6.2. V2 jobs/actions (рекомендуемый путь)
- `POST /v2/jobs` → {command_id, type, params} → {job_id}
- `GET /v2/jobs/{job_id}` → status/progress/result
- `POST /v2/jobs/{job_id}/cancel` → best‑effort cancel  
Это приближает поведение к ROS2 Actions: goal/feedback/result/cancel.

## 7. Observability & Ops
- `/health/live`: процесс жив
- `/health/ready`: RobotReady == true
- Логи: structured JSON, поля `request_id`, `command_id`, `job_id`
- Метрики: reconnects, command_latency, command_failures, busy_rejects

## 8. Рекомендуемая структура проекта (V2)

```
xarm_service/
├── app/
│   ├── routes.py
│   ├── types.py
│   ├── config.py
│   ├── health.py            # live/ready endpoints
│   └── di.py                # wiring: singletons
└── drivers/xarm_driver/
    ├── sdk/                 # thin wrapper around XArmAPI
    ├── actor/
    │   ├── actor.py         # RobotActor
    │   ├── commands.py      # Command, Result, policies
    │   └── cancel.py
    ├── connection/
    │   ├── manager.py
    │   └── lifecycle.py     # register/release callbacks
    ├── state/
    │   ├── store.py         # StateStore
    │   ├── models.py
    │   └── readiness.py     # Ready gate computation
    ├── usecases/
    │   ├── movement.py
    │   ├── gripper.py
    │   └── recovery.py
    └── utils/
        ├── logging.py
        └── validation.py
```

## 9. Диаграммы
См. `diagrams/architecture.mmd` и `diagrams/state_machine.mmd`.
