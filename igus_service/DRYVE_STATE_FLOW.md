# dryve D1: правильная работа со состояниями (CiA402)

Этот документ фиксирует рабочую схему управления состояниями привода для `igus_service`.

Основано на:
- драйверной реализации в `drivers/dryve_d1/api/drive.py`
- motion-слоях `profile_velocity.py` и `profile_position.py`
- проверках на реальном железе (jog + move + stop)

## 1) Ключевые правила

1. Перед любым движением не должно быть `FAULT`.
2. Для движения должен быть активен `REMOTE` (DI7 Enable / Statusword bit 9).
3. Для команд движения привод должен быть в `OPERATION_ENABLED`.
4. После `STOP`/`quick_stop` и после некоторых переходов HALT может остаться активным — перед новым движением HALT надо сбрасывать.
5. Для `Profile Position` команда старта (Controlword bit 4 / NEW_SET_POINT) должна иметь видимый импульс (не слишком короткий).

## 2) Рабочий flow для Jog (PV, mode=3)

### Jog start
1. Проверить связь, `FAULT == false`, `REMOTE == true`.
2. Если `operation_enabled == false` → `enable_operation()`.
3. Сбросить HALT (если ранее был выставлен).
4. Перевести режим в PV (mode=3), дать `mode_settle_s`.
5. Записать target velocity, подать pulse NEW_SET_POINT.
6. Запустить keepalive TTL.

### Jog stop
1. Остановить jog (velocity→0 / release).
2. Запланировать delayed shutdown (энергосбережение), но:
   - если стартует новое движение, таймер должен быть отменён.

## 3) Рабочий flow для Move to Position (PP, mode=1)

### Move start
1. Отменить delayed shutdown timer от jog.
2. Остановить активный jog (если есть), дать короткую паузу на decel.
3. Проверить `FAULT`, `REMOTE`.
4. Если `operation_enabled == false` → `enable_operation()`.
5. Сбросить HALT.
6. Перевести режим в PP (mode=1), подождать `mode_settle_s`.
7. Записать target position + profile params.
8. Подать pulse NEW_SET_POINT:
   - set bit4
   - **подержать минимум 1 system cycle**
   - clear bit4
9. Ждать handshake/target reached.

### Move finish
- В `finally` вызвать `disable_voltage()` (best effort), чтобы не держать ток в простое.

## 4) STOP и прерывание движения

### STOP (quick_stop/stop)
1. Сначала выставить внутренний abort event (чтобы waiting-циклы мгновенно прерывались).
2. Применить HALT как физический страховочный stop.
3. Выполнить mode-aware stop (PP/PV).

### Ожидаемое поведение
- Если пользователь нажимает STOP во время move:
  - in-progress move должен завершиться как `aborted`
  - повторный move должен стартовать без «залипания».

## 5) Типичные симптомы и причины

### Симптом
`Timeout: target_reached never cleared after new set-point`

### Частая причина
Привод не принял старт PP-команды (бит4 импульс был слишком короткий или режим не успел примениться).

### Что уже учтено в рабочем flow
- mode settle перед стартом PP
- удержание bit4 на 1 system cycle перед clear
- отмена jog shutdown timer перед move

## 6) Мини-чеклист перед эксплуатацией

1. DI7 Enable физически активен.
2. Нет FAULT (или выполнен fault reset).
3. Homing выполнен (если обязателен по сценарию).
4. После jog -> move запускается без STOP.
5. После move привод уходит из OPERATION_ENABLED (нет лишнего тока в простое).

## 7) Файлы-источники поведения

- `drivers/dryve_d1/api/drive.py`
- `drivers/dryve_d1/motion/profile_velocity.py`
- `drivers/dryve_d1/motion/profile_position.py`
- `drivers/dryve_d1/cia402/state_machine.py`
- `drivers/dryve_d1/od/statusword.py`
- `drivers/dryve_d1/od/controlword.py`

## 8) Сквозная трассировка операций (API ↔ Driver)

Для командных `/drive/*` endpoint-ов в ответе возвращается:

- `meta.request_id` — ID HTTP-запроса (middleware)
- `meta.command_id` — ID команды (генерируется на командных endpoint-ах)

Для legacy endpoint-ов `/move`, `/reference`, `/fault_reset` в ответе возвращается:

- `request_id`
- `command_id`

Для быстрого runtime-check доступен endpoint:

- `GET /drive/trace/latest` → последний in-memory trace (`request_id`, `command_id`, `op_id`, `operation`, `ts`)

В SSE `/drive/events` для команд публикуется `type=command` с payload:

- `command_id`
- `request_id`
- `op_id`
- `operation`
- `result`

В драйверных логах для `move/jog/stop/quick_stop` используется:

- `op_id` — короткий ID операции внутри `drive.py`

### Как использовать на практике

1. Взять `request_id` и `command_id` из HTTP-ответа `/drive/*`.
2. Проверить подтверждающее событие в SSE `/drive/events` (`type=command`, совпадает `command_id`).
3. Взять из этого же события `op_id`.
4. Найти в логах строки по `request_id` (уровень API) и по `op_id` (уровень драйвера).
5. Собрать полную цепочку:
   - вход запроса
   - precondition checks (`fault/remote/operation_enabled`)
   - mode/HALT шаги
   - completion/abort/timeout

### Что обязательно смотреть при инциденте

- `statusword` и декодированные флаги в сообщениях timeout
- `mode_display` в сообщениях timeout
- наличие `disable_voltage` в `finally` после `move_to_position`
- наличие `abort_event set` для `stop/quick_stop`

---
Если flow меняется, обновляйте этот документ вместе с кодом, чтобы не терять рабочие инварианты между PV/PP/STOP переходами.
