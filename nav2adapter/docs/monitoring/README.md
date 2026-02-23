# Local Monitoring Stack (Prometheus + Grafana)

## Quick Start

Run from this directory:

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Services:
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)

Prometheus scrapes:
- `http://host.docker.internal:7905/ops/reliability.prom`

## Requirements

- Nav2Adapter should be running and reachable on host port `7905`.
- Docker Engine with Compose plugin.

## Verify metrics

```bash
curl -s http://localhost:7905/ops/reliability.prom | head
```

In Prometheus UI, try query:

```promql
nav2adapter_reliability_degraded
```

## Grafana Dashboard

The dashboard **nav2adapter Reliability** is auto-provisioned on startup.

Open http://localhost:3000 → login `admin / admin` → the dashboard is available
immediately with panels:

| Row | Panels |
|-----|--------|
| 1   | Degraded stat, MQTT event failures, MQTT skipped (disconnected) |
| 2   | EventBus drop rate, Persistence drop rate, MQTT event failure rate |
| 3   | MQTT publish latency avg, MQTT publish latency max |
| 4   | Symovo long-poll latency avg, Persistence write latency avg |
| 5   | All counters table (sorted by count desc) |

Files:
- `dashboards/nav2adapter-reliability.json` — dashboard definition
- `provisioning/datasources/prometheus.yml` — Prometheus datasource
- `provisioning/dashboards/dashboards.yml` — dashboard provider config

## Stop

```bash
docker compose -f docker-compose.monitoring.yml down
```

## Notes

- `host.docker.internal` mapping is enabled via `extra_hosts` and should work on Linux with Docker >= 20.10.
- For production, replace static target with service discovery and secure Grafana credentials.
