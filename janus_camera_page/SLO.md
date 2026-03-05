# Camera Stack — Service Level Objectives (SLOs)

> Owner: Camera team  
> Scope: WebRTC streaming on dual Raspberry Pi 5 nodes (color + depth)  
> Effective: 2026-03-04  
> Review cadence: monthly

---

## 1. Definitions

| Term | Meaning |
|------|---------|
| **ICE connect time** | Elapsed from `new RTCPeerConnection()` to `iceConnectionState === 'connected'` |
| **TTFF** | Time-to-first-frame — elapsed from page-load to first decoded video frame |
| **MTTR** | Mean time to recover — elapsed from fault detection (watchdog) to stream-healthy |
| **Uptime** | Fraction of 1-minute windows where `/healthz` returns HTTP 200 |

## 2. SLO Targets

| Metric | Target | Measurement | Burn-rate alert |
|--------|--------|-------------|-----------------|
| **ICE connect time (p95)** | ≤ 5 s | `camstack_ice_connect_duration_seconds` histogram (P95) | >8 s over 5-min window |
| **TTFF (p95)** | ≤ 8 s | Client telemetry `time_to_first_frame_ms` (P95) | >12 s over 5-min window |
| **MTTR (p95)** | ≤ 60 s | Duration from `camstack_stream_active == 0` to `== 1` | >120 s over 10-min window |
| **Stream availability** | ≥ 99.0 % | `/healthz` scrape (1 minute windows) | <97 % over rolling 1 h |
| **Packet loss (video inbound)** | ≤ 1 % | Client telemetry `packets_lost / packets_received` | >3 % over 5-min window |

## 3. Error Budget

- **Monthly error budget**: 100 % − 99.0 % = **1 %** ≈ 7.3 hours/month of downtime.
- When burn-rate exceeds 2×, stop feature work and triage.
- When budget exhausted (>7.3 h cumulative downtime), freeze deploys until root-cause resolved.

## 4. Measurement Architecture

```
┌────────────┐  POST /telemetry  ┌──────────────┐  GET /metrics  ┌────────────┐
│  Browser    │ ───────────────→  │  FastAPI      │ ←───────────── │ Prometheus │
│  (player)   │                   │  (Pi node)    │                │            │
│  getStats() │                   │  counters +   │                │  PromQL    │
│  Telemetry  │                   │  histograms   │                │  + Grafana │
│  adapter    │                   │  /metrics     │                │            │
└────────────┘                    └──────────────┘                └────────────┘
```

### Key Prometheus queries

| SLO | PromQL |
|-----|--------|
| ICE p95 | `histogram_quantile(0.95, rate(camstack_ice_connect_duration_seconds_bucket[5m]))` |
| Stream avail | `avg_over_time(camstack_stream_active[1h])` |
| Recovery rate | `rate(camstack_watchdog_escalations_total[5m])` |
| Mode health | `camstack_system_mode` (0 = nominal) |

## 5. Alerting Thresholds

| Alert | Condition | Severity |
|-------|-----------|----------|
| `CamstackStreamDown` | `camstack_stream_active == 0` for 2 min | **critical** |
| `CamstackICESlow` | ICE p95 > 8 s for 5 min | warning |
| `CamstackHighPacketLoss` | loss rate > 3 % for 5 min | warning |
| `CamstackThermalCritical` | `camstack_cpu_temp_celsius > 80` for 1 min | critical |
| `CamstackLadderExhausted` | `camstack_recovery_ladder_level >= 4` for 5 min | critical |
| `CamstackSafeMode` | `camstack_system_mode == 3` for 1 min | critical |

## 6. Reporting & Review

- **Dashboard**: Grafana board `camera-stack-slos` (to be provisioned).
- **Weekly**: Automated SLO compliance email via Prometheus Alertmanager.
- **Monthly**: Review SLO targets, error budget consumption, incident post-mortems.

## 7. Exceptions

- **Planned maintenance** (systemd timer reboots, deploy windows) is excluded from uptime calculation.
- **P1.9 hardware change** (color camera USB 3.0 migration) may cause a scheduled outage — pre-announced.
- **External TURN VPS outage** counts against availability only if depth camera (relay-only) is affected.
