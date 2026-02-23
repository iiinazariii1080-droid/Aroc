# API Evolution (v1 façade + v2 jobs)

Дата: 2026-01-28

## 1) Что сохраняем (v1)
- текущие эндпоинты и payload остаются без изменений для клиентов
- внутри: каждый endpoint создаёт `Command` с `command_id` и отдаёт в `CommandService`

## 2) Что добавляем (v2)
Рекомендуемый набор (без поломок v1):

- `POST /v2/jobs`
  - body: { command_id, type, params, policy?, timeout_ms? }
  - resp: { job_id }

- `GET /v2/jobs/{job_id}`
  - resp: { status: queued|running|succeeded|failed|canceled, progress?, result?, error? }

- `POST /v2/jobs/{job_id}/cancel`

Причина: длительные движения и teleop плохо сочетаются с “синхронным HTTP ожиданием”.

## 3) Idempotency
- `command_id` обязателен в v2; для v1 можно генерировать сервером если не пришёл
- повторный `command_id` возвращает тот же результат (если он уже есть)

## 4) Ошибки (рекомендация)
- 503: robot not connected
- 409/423: busy
- 409/423: faulted (needs recovery)
- 400: invalid params
