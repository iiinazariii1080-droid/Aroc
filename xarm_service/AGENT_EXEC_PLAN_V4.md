# xArm Service — Detailed Agent-Executable Plan (V4)
Дата: 2026-01-28

Цель документа: дать **однозначный, машинно‑интерпретируемый** план (MUST/SHALL), чтобы нейросетевые агенты/разработчики
выполняли рефакторинг и добавление safety‑функций **без архитектурных ошибок**, с гарантией:
- детерминированного управления манипулятором,
- плавных движений,
- строгого соблюдения рабочей зоны **40×90×120 cm** (включая «локти»/звенья),
- production‑операционности (health/readiness/метрики/логирование),
- эволюции API без breaking changes.

---

## 0) Контекст и допущения

### 0.1. Термины (глоссарий)
- **SDK**: `XArmAPI` (синхронный, потоконебезопасный с точки зрения параллельных motion-команд).
- **Single-Owner / Single-Writer**: объект SDK существует в **одном месте** и команды в него идут **строго последовательно**.
- **WS (Workspace Box)**: ограниченная зона 3D.
- **Envelope**: программная проверка, что **не только TCP**, но и **все звенья** (capsules/boxes) находятся внутри WS.
- **Command**: атомарное действие робота (move, gripper, reset, enable…).
- **Job**: длительная операция/сценарий, состоящий из команд (v2 модель).
- **ReadinessGate**: вычисляемое состояние “можно ли принимать команды”.

### 0.2. Единицы
- Все геометрические величины — **мм**.
- Время — **сек**.
- Углы — **градусы** (если SDK так ожидает) или **радианы** (если кинематика так считает) — строго фиксировать в контрактах.

---

## 1) Негативные требования (FORBIDDEN, MUST NOT)
Эти правила запрещены. Их нарушение считается дефектом архитектуры.

1. **MUST NOT** вызывать `XArmAPI.*` напрямую из роутеров, usecases, callbacks, тестов бизнес‑логики.
2. **MUST NOT** выполнять `clean_error/clean_warn` или `motion_enable(True)` автоматически на connect().
3. **MUST NOT** исполнять две motion‑команды параллельно (даже если они “разные endpoints”).
4. **MUST NOT** считать безопасность выполненной, если ограничен только TCP (локти/звенья обязаны быть в зоне).
5. **MUST NOT** “глотать” ошибки SDK без классификации и перевода состояния в faulted, если это влияет на безопасность.

---

## 2) Архитектурные инварианты (MUST / SHALL)

### 2.1. Single Owner of SDK (MUST)
- `XArmAPI` создаётся только в `RobotActor` и уничтожается только там.
- Любая команда к роботу идёт в `RobotActor` через очередь `asyncio.Queue[Command]`.

### 2.2. Single-Writer Command Execution (MUST)
- В `RobotActor` есть единственный “executor loop”:
  - берёт следующую команду из очереди,
  - валидирует,
  - исполняет (в threadpool),
  - публикует результат,
  - только потом берёт следующую.

### 2.3. Safety Envelope (MUST)
- Любая команда движения обязана пройти:
  1) **preflight_validate** (целевое состояние допустимо)
  2) **trajectory_validate** (путь дискретизирован и допустим)
  3) **runtime monitor** (выполнение остаётся допустимым)

Если любой шаг провален → команда не исполняется, возвращается ошибка `OUT_OF_WORKSPACE` (или аналог) и состояние остаётся безопасным.

### 2.4. ReadinessGate (MUST)
Команды принимаются только если:
- `connected == true`
- `faulted == false`
- `motion_enabled == true` (для motion/gripper)
- `busy == false` (или политика preempt/queue разрешает)

### 2.5. Explicit Recovery (MUST)
- `recover/reset_faults` и `enable_motion` — отдельные команды, вызываемые явно.
- После fault система должна требовать явного recovery.

---

## 3) Рабочая зона (WS) и координатные кадры

### 3.1. Параметры WS (фиксированы)
- Размер WS: `WS.size = (X=400, Y=900, Z=1200)` мм
- Положение базы в WS: `base_in_ws = (150, 450, 0)` мм

### 3.2. Frames (MUST)
- `ws` — кадр рабочей зоны
- `base` — кадр базы манипулятора
- `tcp` — кадр инструмента

### 3.3. Конфиг трансформации (MUST)
В конфиге хранить:
- `T_ws_base.translation = base_in_ws`
- `T_ws_base.rotation` (yaw/pitch/roll или quaternion) — **явно**.

Если rotation неизвестна — план обязателен:
1) определить физический origin WS,
2) измерить 3 точки (origin, +X, +Y),
3) восстановить rotation,
4) закоммитить конфиг.

### 3.4. WS boundary в base-frame (если rotation=identity)
```
x_min = -150; x_max = 250
y_min = -450; y_max = 450
z_min = 0;    z_max = 1200
```
Добавить `margin` (по умолчанию 40 мм) и зажать внутрь.

---

## 4) Компоненты и контракты (для агентов)

### 4.1. Data Models (Pydantic / dataclasses)
#### Command
Поля (минимум):
- `command_id: str` (UUID)
- `type: Enum` (MOVE_JOINTS, MOVE_POSE, MOVE_LINEAR, GRIP_OPEN, GRIP_CLOSE, STOP, RECOVER, ENABLE_MOTION, DISABLE_MOTION, GET_STATUS)
- `params: dict` (строго валидируемый подтип для каждого `type`)
- `timeout_s: float` (дедлайн на команду)
- `policy: ExecutionPolicy`:
  - `REJECT_IF_BUSY`
  - `QUEUE`
  - `PREEMPT_ACTIVE` (best-effort cancel + start new)

#### CommandResult
- `command_id`
- `status: Enum` (ACCEPTED/REJECTED/RUNNING/SUCCEEDED/FAILED/CANCELED)
- `error_code: Optional[Enum]`
- `error_message: Optional[str]`
- `started_at/finished_at`
- `telemetry_snapshot` (опционально)

#### RobotState (StateStore)
- connection: `connected`, `last_seen`, `reconnects`
- motion: `motion_enabled`, `busy`, `mode`
- faults: `faulted`, `error_code`, `warn_code`
- pose: `tcp_pose`, `joint_angles`
- safety: `in_workspace`, `workspace_margin_mm`, `last_violation`

### 4.2. RobotActor (единственный владелец SDK)
Функции:
- `start() / stop()`
- `enqueue(command) -> Future/Task`
- `connect_loop()` (поддержка reconnect)
- `execute(command)`:
  - `validate_readiness`
  - `validate_safety_preflight`
  - `validate_safety_trajectory`
  - `sdk_call` (threadpool)
  - publish result/state

### 4.3. SafetyEnvelope (software)
Обязательные интерфейсы:
- `fk_all_links(joints) -> {link_name: Pose}`
- `check_links_in_ws(joints) -> CheckResult`
- `check_trajectory(traj_points[]) -> CheckResult`

Геометрия:
- для каждого линка определить capsule:
  - `capsule_local_a`, `capsule_local_b`, `radius`
- преобразовать в ws-frame и проверить “capsule inside box” с дискретизацией.

### 4.4. SafetyMonitor (runtime guard)
- частота: 20–50 Hz
- если `violation`:
  - вызвать `STOP` (soft/hard по возможности)
  - `state.faulted = true`
  - публиковать событие `SAFETY_VIOLATION`

---

## 5) Motion Policy (плавность и предсказуемость)

### 5.1. Единый motion config (MUST)
В конфиге:
- `tcp_speed`, `tcp_acc`
- `joint_speed`, `joint_acc`
- `jerk_limit` (если поддерживается)
- `blend_radius` (если поддерживается)
- `safe_high_pose_joints` (поза, где локти гарантированно внутри WS)

### 5.2. Стандартные примитивы движения (SHALL)
Для pick/place сценариев использовать шаблон:
1) `MOVE_JOINTS -> safe_high_pose`
2) `MOVE_JOINTS/MOVE_LINEAR -> pre_pick_above`
3) `MOVE_LINEAR -> pick`
4) `GRIP_CLOSE/OPEN`
5) `MOVE_LINEAR -> retreat`
6) `MOVE_JOINTS -> safe_high_pose`

### 5.3. Teleop/Joystick (SHALL)
- отдельный режим `mode=TELEOP`:
  - **deadman** (если нет команды > 200 ms → STOP)
  - rate limit (например 10–20 Hz)
  - low-pass на скорости
  - envelope-check на каждом шаге (и STOP при нарушении)

---

## 6) API Strategy (совместимость + правильная модель)

### 6.1. v1 façade (MUST keep)
- Существующие endpoints остаются, но внутри:
  - формируют `Command`
  - отправляют в `CommandService`
  - либо ждут completion, либо возвращают ACK + command_id (без breaking change — через опциональные поля)

### 6.2. v2 jobs/actions (SHALL add)
- `/v2/jobs` create → returns `job_id`
- `/v2/jobs/{id}` status
- `/v2/jobs/{id}/cancel`

Jobs используют CommandActor, но добавляют:
- прогресс
- feedback
- результат

---

## 7) Рефакторинг: фазы и gate‑checklists

Ниже — порядок работ. **Переход к следующей фазе запрещён**, пока gate‑чеклист не выполнен.

### Phase 0 — Stabilize & Freeze (1–2 PR)
Цели:
- устранить очевидные дефекты/дубли, зафиксировать baseline поведения.

Deliverables:
- lint/format, базовые тесты запуска, фикс импорта/ошибок.

**Gate 0 Checklist (MUST):**
- [ ] сервис стартует и отвечает `/health/live`
- [ ] нет дубликатов RobotMain (или они помечены DEPRECATED и не используются новым кодом)
- [ ] добавлен `command_id` в логи (middleware)

---

### Phase 1 — RobotActor Skeleton (Single-Owner) (1–3 PR)
Цели:
- внедрить RobotActor + очередь + StateStore + readiness.

Deliverables:
- `RobotActor` (connect_loop, queue, execute stub)
- `StateStore` + `ReadinessGate`
- `/health/ready` учитывает connected/faulted/motion_enabled

**Gate 1 Checklist (MUST):**
- [ ] SDK создаётся только в RobotActor (grep подтверждает)
- [ ] команды в SDK не вызываются из роутеров/usecases
- [ ] при отключении SDK состояние становится `connected=false`
- [ ] есть unit тест readiness gate

---

### Phase 1A — Workspace Safety Foundation (обязательная ранняя) (2–4 PR)
Цели:
- формализовать WS frames, включить hardware reduced boundary, добавить software envelope preflight.

Deliverables:
- конфиг `workspace.yaml` (WS.size, T_ws_base, margin)
- функция вычисления base-boundary из WS + margin
- установка reduced mode/boundary при connect (идемпотентно)
- `SafetyEnvelope.check_links_in_ws(joints)` (минимальная реализация)
- preflight для `MOVE_*` команд

**Gate 1A Checklist (MUST):**
- [ ] любая цель, выводящая link за WS, отклоняется ДО движения
- [ ] boundary/margin задокументированы и версионируются
- [ ] добавлены unit тесты: in/out cases для envelope
- [ ] recover/enable не выполняются автоматически

---

### Phase 2 — Vertical Slice: 2 операции через Actor (1–2 PR)
Цели:
- полностью перевести `GET_STATUS` и одну motion‑команду через actor+envelope.

Deliverables:
- `GET_STATUS` → actor (threadpool read)
- `MOVE_JOINTS` или `MOVE_POSE` → actor
- CommandResult статусы

**Gate 2 Checklist (MUST):**
- [ ] при параллельных HTTP вызовах движение не выполняется конкурентно
- [ ] busy-policy: второй move получает 409/423 или queued (по конфигу)
- [ ] motion проходит preflight envelope

---

### Phase 3 — TrajectoryValidator + Runtime SafetyMonitor (2–5 PR)
Цели:
- гарантировать “локти внутри” не только на цели, но на всём пути + мониторинг.

Deliverables:
- `TrajectoryValidator` (discretize N points; N configurable)
- runtime `SafetyMonitor` 20–50 Hz
- реакция на нарушение: STOP + FAULTED

**Gate 3 Checklist (MUST):**
- [ ] траектория проверяется дискретизацией (не только целевая поза)
- [ ] SafetyMonitor останавливает движение при нарушении
- [ ] событие/лог `SAFETY_VIOLATION` содержит: link, координату, margin
- [ ] есть integration test с mock траекторией (out of WS)

---

### Phase 4 — Full Migration of Endpoints + Remove Duplicates (N PR)
Цели:
- все endpoints используют CommandService/RobotActor.
- удалить legacy RobotMain*.

Deliverables:
- маппинг каждого endpoint → Command type
- общая ошибка-модель
- удаление дубликатов файлов

**Gate 4 Checklist (MUST):**
- [ ] grep: нет прямых SDK вызовов вне RobotActor
- [ ] все motion endpoints проходят envelope+trajectory checks
- [ ] faulted/busy возвращают корректные HTTP статусы
- [ ] документация API обновлена

---

### Phase 5 — Motion Smoothness & Profiles (2–4 PR)
Цели:
- предсказуемая плавность: speed/acc/jerk, safe poses, blending.

Deliverables:
- конфиг motion limits
- safe_high_pose и шаблоны pick/place
- телеметрия времени исполнения

**Gate 5 Checklist (SHALL):**
- [ ] движения без “дёрганий” (проверка на реальном роботе)
- [ ] есть параметризация speed/acc для режимов (normal/reduced/teleop)
- [ ] safe_high_pose подтверждён envelope-check

---

### Phase 6 — Observability, Metrics, Hardening (ongoing)
Deliverables:
- structured logs, metrics counters/histograms
- tracing request_id → command_id
- rate limiting teleop
- security (authz) если требуется

**Gate 6 Checklist (SHALL):**
- [ ] dashboards/метрики позволяют увидеть reconnects, faults, safety stops
- [ ] есть runbook “как восстановить после fault”

---

## 8) Code Review Checklists (обязательные для каждого PR)

### 8.1. Architecture Checklist (MUST)
- [ ] нет SDK вызовов вне RobotActor
- [ ] командный поток: Router → CommandService → Actor → SDK
- [ ] callbacks не запускают движения

### 8.2. Safety Checklist (MUST)
- [ ] для motion есть preflight + trajectory validate
- [ ] runtime monitor включён (или явно выключен в dev с флагом и предупреждением)
- [ ] нет auto recover/enable

### 8.3. Motion Checklist (SHALL)
- [ ] speed/acc берутся из конфигов
- [ ] есть safe poses для сценариев
- [ ] teleop имеет deadman + rate limit

### 8.4. QA Checklist (MUST)
- [ ] unit tests для новых модулей
- [ ] integration tests для actor flow
- [ ] обновлены docs/diagrams при изменении контрактов

---

## 9) Acceptance Criteria (глобальные)

Система считается соответствующей требованиям, если:
1) **Невозможно** заставить манипулятор выйти за WS (TCP + звенья) через любые endpoints/скрипты (при включенном safety).
2) При попытке выйти за WS:
   - команда отклоняется до движения,
   - либо runtime guard стопает и переводит в fault.
3) Нет конкурентных команд в SDK (доказуемо через архитектуру + тесты).
4) Recovery требует явного действия.
5) Плавность управляется конфигом (speed/acc) и воспроизводима.

---
