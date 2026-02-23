## AE.HUB Navigation Backend (Symovo machine-owned)

Этот сервис проксирует команды навигации AE.HUB в Symovo (HTTP API) и публикует статусы/события в MQTT, а также отдаёт события через SSE.

### HTTP

- **Health**: `GET /healthz`
- **Ops reliability**:
  - `GET /livez`
  - `GET /readyz`
  - `GET /ops/reliabilityz`
  - `GET /ops/reliability.prom`
- **UI (MVP)**:
  - `GET /api/v1/robots`
  - `GET /api/v1/robots/{robot_id}/positions`
  - **SSE events**: `GET /api/v1/robots/{robot_id}/events`

### Symovo read-only API (map + lidar + SLAM)

Новые диагностические и визуализационные роуты под префиксом `/api/v1/symovo`:

- **Map**
  - `GET /api/v1/symovo/map`
  - `GET /api/v1/symovo/map/{map_id}`
  - `GET /api/v1/symovo/map/{map_id}/wait_for_changes?since=now&timeout=30`
  - `GET /api/v1/symovo/map/{map_id}/full.png`
  - `GET /api/v1/symovo/map/{map_id}/{zoom}/{x}/{y}.png`
  - `GET /api/v1/symovo/map/slam/slam.png`

- **Lidar / SLAM**
  - `GET /api/v1/symovo/lidar/scan.png`
  - `GET /api/v1/symovo/lidar/raw` (capability endpoint)
  - `GET /api/v1/symovo/slam/state`
  - `GET /api/v1/symovo/slam/pose/station`
  - `GET /api/v1/symovo/slam/pose/reflector`

Ограничения текущего MVP:
- Все новые роуты только `GET` (read-only).
- `raw` lidar ranges/point-cloud сейчас не доступны в `nav2adapter`; endpoint
  `GET /api/v1/symovo/lidar/raw` возвращает capability-ответ с `supported=false`.

Короткие примеры:

```bash
curl -s http://localhost:8010/api/v1/symovo/map | jq
curl -s http://localhost:8010/api/v1/symovo/slam/state | jq
curl -o scan.png http://localhost:8010/api/v1/symovo/lidar/scan.png
```

### MQTT

#### Inbound (commands)
- `aroc/robot/{robot_id}/commands/navigateTo`
- `aroc/robot/{robot_id}/commands/cancel`

#### Outbound (status)
- `aroc/robot/{robot_id}/status/navigation`
- `aroc/robot/{robot_id}/status/position`

#### Outbound (events: ack/state/result)
По умолчанию:
- `aroc/robot/{robot_id}/events/ack`
- `aroc/robot/{robot_id}/events/state`
- `aroc/robot/{robot_id}/events/result`

Префикс можно переопределить через `MQTT_EVENTS_PREFIX`.

### Конфигурация

См. пример в `env.example` (dot-файлы могут быть скрыты/запрещены политиками окружения).

### Reliability runbook

Операторский гайд с профилями порогов и шагами реагирования:
- `docs/RELIABILITY_RUNBOOK.md`
- `docs/ALERT_RULES.md`
- `docs/alerts.prometheus.yml`
- `docs/prometheus.scrape.yml`
- `docs/monitoring/README.md`

### Persistence

Для восстановления идемпотентности после рестартов сохраняется mapping `command_id → transport_id` и terminal `result.*` в JSON-файле:
- `PERSISTENCE_PATH` (по умолчанию `data/state.json`)

