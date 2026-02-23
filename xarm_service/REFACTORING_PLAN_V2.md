# Refactoring Plan — V2 (Single‑Writer RobotActor)

Дата: 2026-01-28

## 0. Общие принципы миграции
- **Не ломать текущие endpoints**: v1 продолжает работать, меняется внутренняя реализация.
- **Вертикальные срезы**: переносить по 1‑2 операции end‑to‑end через новую архитектуру.
- **Инвариант №1 с первого дня**: все mutating операции идут через RobotActor.

## 1. Фазы и deliverables

### Phase 0 — Quick Stabilize (0.5–1 день)
Deliverables:
- фикс критических багов (imports, дубликаты исключений, pprint, autotake/drop/take)
- заморозить публичный API (контракты, модели)  
Acceptance:
- сервис стартует стабильно, базовые endpoints не падают

### Phase 1 — Skeleton: StateStore + Health + DI (1–2 дня)
Deliverables:
- `StateStore` с потокобезопасным доступом (async)
- `ReadinessGate` + `/health/live`, `/health/ready`
- wiring DI: singletons для store/actor/service  
Acceptance:
- health endpoints отражают состояние соединения (пусть пока mock)

### Phase 2 — Introduce RobotActor (2–3 дня)
Deliverables:
- RobotActor loop + command queue + результаты
- ConnectionLifecycle: callbacks register/release один раз
- reconnect loop + backoff  
Acceptance:
- `get_status` и одна “безопасная” команда проходят через actor

### Phase 3 — Vertical slice: movement + cancel policy (2–4 дня)
Deliverables:
- перенос `move_to_pose`/`move_joints` через CommandService → Actor
- policy busy/preempt/reject (настраиваемо)
- idempotency по `command_id` (LRU cache на результаты)  
Acceptance:
- повтор одного `command_id` не запускает движение второй раз
- при busy сервис возвращает предсказуемый 409/423 (или очередь)

### Phase 4 — Gripper & Recovery (2–4 дня)
Deliverables:
- gripper ops через actor
- явные команды: `recover_faults`, `enable_motion`, `disable_motion`, `stop`
- запрет “auto clean_error/motion_enable” в connect()  
Acceptance:
- ошибки требуют явного recover; connect не “сам чинит” робота

### Phase 5 — Remove duplicates & simplify modules (2–3 дня)
Deliverables:
- удалить 15+ RobotMain, оставить один Controller/UseCase слой
- унифицировать исключения и код ошибок  
Acceptance:
- нет дубликатов логики, критические ветки покрыты тестами

### Phase 6 — Observability + Tests hardening (2–4 дня)
Deliverables:
- structured logs, метрики, trace поля
- unit tests (policies, idempotency, readiness)
- integration tests (actor with mocked SDK)
- hardware smoke сценарии  
Acceptance:
- стабильные тесты в CI; документация по эксплуатации

## 2. Риски и митигация
- SDK не thread‑safe → single‑writer actor + запрет прямых вызовов.
- callbacks параллельны → callbacks только пишут в StateStore, без движения.
- длительные движения по HTTP таймаутят → v2 jobs; для v1 ограничить timeouts/дать “async mode”.
- auto recovery небезопасен → разделение connect/recover/enable_motion.

## 3. Definition of Done (DoD)
- Вся мутация робота идёт через actor
- Есть readiness/health
- Есть политика busy/preempt
- Есть явный recovery
- Есть тест‑контур и smoke сценарии
