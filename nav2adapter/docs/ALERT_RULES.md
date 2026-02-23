# Alert Rules (Reliability)

Этот документ описывает базовые alert-правила для метрик из:
- `GET /ops/reliability.prom`

Machine-readable файлы в репозитории:
- `docs/alerts.prometheus.yml`
- `docs/prometheus.scrape.yml`

Цель: быстро обнаруживать деградацию MQTT ↔ Nav2Adapter и проблемы контроллера/хранилища.

## 1) Базовый health alert

### `Nav2AdapterReliabilityDegraded`

- **Metric**: `nav2adapter_reliability_degraded`
- **Severity**: warning
- **Condition**: `== 1` в течение 2 минут
- **Action**: открыть `/ops/reliabilityz`, посмотреть `reasons`, применить runbook

Пример (Prometheus style):

```yaml
- alert: Nav2AdapterReliabilityDegraded
  expr: nav2adapter_reliability_degraded == 1
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "Nav2Adapter reliability degraded"
    description: "Service is alive, but reliability thresholds are exceeded"
```

## 2) Потери и ошибки (rate-based)

### `Nav2AdapterMqttEventFailureRateHigh`

- **Metric**: `nav2adapter_reliability_rate_per_sec{name="mqtt.publish.event.failure"}`
- **Warning**: `> 0.01` for 3m
- **Critical**: `> 0.05` for 1m

### `Nav2AdapterEventBusDropRateHigh`

- **Metric**: `nav2adapter_reliability_rate_per_sec{name="eventbus.publish.drop_oldest"}`
- **Warning**: `> 0.01` for 3m
- **Critical**: `> 0.05` for 1m

### `Nav2AdapterPersistenceDropRateHigh`

- **Metric**: `nav2adapter_reliability_rate_per_sec{name="persistence.enqueue.drop_queue_full"}`
- **Warning**: `> 0.005` for 3m
- **Critical**: `> 0.02` for 1m

## 3) Latency (avg/max)

### `Nav2AdapterMqttEventLatencyHigh`

- **Avg metric**: `nav2adapter_reliability_duration_avg_ms{name="mqtt.publish.event.latency_s"}`
- **Warning**: `> 1500` for 5m
- **Critical**: `> 3000` for 2m

### `Nav2AdapterSymovoLongpollLatencyHigh`

- **Avg metrics**:
  - `nav2adapter_reliability_duration_avg_ms{name="symovo.transport_wait_for_changes.latency_s"}`
  - `nav2adapter_reliability_duration_avg_ms{name="symovo.amr_wait_for_changes.latency_s"}`
- **Warning**: `> 35000` for 5m
- **Critical**: `> 50000` for 2m

### `Nav2AdapterPersistenceWriteLatencyHigh`

- **Avg metrics**:
  - `nav2adapter_reliability_duration_avg_ms{name="persistence.writer.upsert.latency_s"}`
  - `nav2adapter_reliability_duration_avg_ms{name="persistence.writer.upsert_session.latency_s"}`
  - `nav2adapter_reliability_duration_avg_ms{name="persistence.writer.delete.latency_s"}`
  - `nav2adapter_reliability_duration_avg_ms{name="persistence.writer.delete_session.latency_s"}`
- **Warning**: `> 250` for 5m
- **Critical**: `> 500` for 2m

## 4) Counter guards (накопительные)

Полезно как secondary signal:

- `nav2adapter_reliability_counter{name="mqtt.publish.event.failure"}` monotonic increase
- `nav2adapter_reliability_counter{name="eventbus.publish.drop_oldest"}` > 0
- `nav2adapter_reliability_counter{name="persistence.enqueue.drop_queue_full"}` > 0

Рекомендация: использовать вместе с rate-based правилами.

## 5) Быстрые действия по severity

### Warning

1. Проверить `/ops/reliabilityz` и `reasons`.
2. Сопоставить тип проблемы: MQTT / EventBus / persistence / Symovo.
3. Наблюдать 5–10 минут после стабилизации.

### Critical

1. Снизить входную нагрузку (если возможно).
2. Проверить сеть до MQTT и Symovo.
3. Проверить I/O контейнера/узла.
4. Если деградация не уходит — контролируемый рестарт сервиса.

## 6) Scrape пример

```yaml
scrape_configs:
  - job_name: nav2adapter-reliability
    metrics_path: /ops/reliability.prom
    static_configs:
      - targets: ["nav2adapter-host:7905"]
```

    Для быстрого старта можно использовать готовый шаблон:
    - `docs/prometheus.scrape.yml`
