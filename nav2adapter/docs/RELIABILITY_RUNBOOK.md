# Reliability Runbook (MQTT ↔ Nav2Adapter)

Этот документ нужен для быстрой эксплуатации и дебага деградации.

## 1) Ключевые endpoints

- `GET /livez` — liveness + reliability summary
- `GET /readyz` — readiness + reliability summary
- `GET /ops/reliabilityz` — compact диагностика: top counters, top duration, top rates
- `GET /ops/reliability.prom` — Prometheus text export

Пример:

```bash
curl -s http://localhost:7905/ops/reliabilityz | python3 -m json.tool
curl -s http://localhost:7905/ops/reliability.prom
```

## 2) Что означает `degraded`

`degraded=true` — сервис жив, но уже есть условия риска:

- потери событий/сообщений,
- рост ошибок публикации,
- рост latency (средней) в критичных путях,
- высокий rate ошибок/дропов за rolling-окно.

## 3) Профили порогов

Ниже стартовые значения. Подбирайте под реальную нагрузку.

### DEV (быстро ловить аномалии)

```env
HEALTH_EVENTBUS_DROP_MAX=0
HEALTH_PERSISTENCE_DROP_MAX=0
HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX=0

HEALTH_LATENCY_MIN_SAMPLES=3
HEALTH_MQTT_EVENT_LATENCY_MAX_MS=1200
HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS=25000
HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS=150

HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S=0.005
HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S=0.003
HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S=0.005
```

### STAGE (баланс шум/чувствительность)

```env
HEALTH_EVENTBUS_DROP_MAX=0
HEALTH_PERSISTENCE_DROP_MAX=0
HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX=1

HEALTH_LATENCY_MIN_SAMPLES=10
HEALTH_MQTT_EVENT_LATENCY_MAX_MS=1500
HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS=35000
HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS=250

HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S=0.01
HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S=0.005
HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S=0.01
```

### PROD (консервативно, минимум false positives)

```env
HEALTH_EVENTBUS_DROP_MAX=0
HEALTH_PERSISTENCE_DROP_MAX=0
HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX=2

HEALTH_LATENCY_MIN_SAMPLES=20
HEALTH_MQTT_EVENT_LATENCY_MAX_MS=2000
HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS=45000
HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS=300

HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S=0.02
HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S=0.01
HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S=0.02
```

## 4) Быстрые действия при деградации

### Сценарий A: `mqtt_event_publish_failure` / `mqtt_event_failure_rate`

1. Проверить доступность брокера и TLS/credentials.
2. Проверить network flap (loss/latency).
3. Если rate растет — временно ограничить нагрузку команд.

### Сценарий B: `eventbus_drop_oldest` / `eventbus_drop_rate`

1. Это риск потери событий для UI/SSE/MQTT.
2. Снизить входной поток команд/событий, проверить узкие места обработки.
3. Проверить задержки в consumer-side (SSE clients, MQTT publish).

### Сценарий C: `persistence_queue_drop` / `persistence_drop_rate`

1. Это риск потери recovery-истории.
2. Проверить I/O (disk latency), нагрузку и лимиты контейнера.
3. Временное действие: уменьшить churn команд и стабилизировать storage.

### Сценарий D: latency reasons

1. Если `mqtt_event_latency` — проверять broker/network.
2. Если `symovo_*_longpoll_latency` — проверять контроллер Symovo и сеть до него.
3. Если `persistence_*_latency` — проверять filesystem/container I/O.

## 5) Минимальный operational цикл

1. Мониторить `/readyz` и `/ops/reliabilityz`.
2. При `degraded=true` фиксировать `reasons` и соответствующие counters/rates.
3. Применять профиль порогов по окружению (dev/stage/prod).
4. После стабилизации проверить, что `reasons` исчезли, а rates вернулись к baseline.

## 6) Alert rules

Готовые правила warning/critical и scrape-пример:
- `docs/ALERT_RULES.md`

## 7) Local monitoring stack

Для локального запуска Prometheus + Grafana:
- `docs/monitoring/README.md`
