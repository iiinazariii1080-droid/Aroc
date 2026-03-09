# Camera Stack Reliability Audit — Gap Analysis & Prioritized Checklist

> Generated: 2026-03-04 | Based on live SSH audit of both Pi nodes + codebase review
> Reference: [deep-research-report.md](marscam_camera_webrtc_docs/deep-research-report.md)

---

## Architecture Summary (from live audit)

| Node | IP | Role | Janus | Interfaces |
|------|----|------|-------|------------|
| Color (gateway) | 192.168.1.10 (br0) / 192.168.10.112 (wlan0) | V4L2 camera, gateway, Docker host | port 8088/8188 | eth0→br0, wlan0→uplink |
| Depth | 192.168.1.55 (wlan0) | RealSense D435, depth pipeline | port 8088/8188 | wlan0 only, default via .10 |

### Key Findings from SSH Audit

**Positive:**
- ✅ RealSense on USB 3.0 (Bus 003, 5000M) on depth node
- ✅ NTP synced on both nodes, RTC present
- ✅ Hardware watchdog available on both (`/dev/watchdog`, BCM2835)
- ✅ `ip_forward=1` on color node (gateway routing works)
- ✅ Tailscale VPN on depth node for out-of-band access
- ✅ Systemd failsafe chain exists: `OnFailure=realsense-failsafe → usb-reset`
- ✅ Client-side player has 3-level recovery ladder + state machine
- ✅ `no_media_timer=30` set on depth Janus
- ✅ `pkt_size=1200` in ffmpeg RTP — avoids IP fragmentation

**Critical Gaps (remaining):**
- ❌ Color camera on USB 2.0 (Bus 004, 480M) — should be on USB 3.0
- ✅ ~~Color Janus missing `no_media_timer`, `rtp_port_range` — FIXED 2026-03-04~~
- ✅ ~~Color Janus Admin API disabled — ENABLED 2026-03-04 (port 7088)~~
- ✅ ~~Depth Janus Admin API no auth secret — ADDED 2026-03-04~~
- ✅ ~~No `dtls_mtu` on either node — SET to 1200 on both 2026-03-04~~
- ✅ ~~No `slowlink_threshold` on either node — SET to 50 on both 2026-03-04~~
- ✅ ~~No hardware watchdog — RuntimeWatchdogSec=30 on both 2026-03-04~~
- ✅ ~~`ice_ignore_list` missing `tailscale` on color — ADDED 2026-03-04~~
- ❌ Color node `rtp_port_range` in streaming config uses port 5004 — overlaps with depth
- ✅ ~~`nat_1_1_mapping` on color node points to possibly stale public IP — AUTO-UPDATE cron 2026-03-04~~
- ✅ ~~No iptables rules (no firewall at all on either node) — iptables deployed 2026-03-04~~
- ✅ ~~TURN credentials plaintext in source code — REMOVED from settings.py / janus.py~~
- ❌ No coturn config in repo (inactive on color node, external VPS at 82.165.177.194)
- ❌ No QoS/traffic classification on either node

---

## Prioritized Checklist

### P0 — Blocking autonomous operation (do first)

| # | Task | Domain | Status | Where |
|---|------|--------|--------|-------|
| P0.1 | ~~Implement hierarchical FDIR recovery ladder (5 levels)~~ | Pipeline | ✅ DONE | `app/services/recovery_ladder.py` |
| P0.2 | ~~Implement system operating modes (Nominal/Degraded/Local-only/Safe)~~ | System | ✅ DONE | `app/services/system_mode.py` |
| P0.3 | ~~Structured FDIR event logging (ring buffer + jsonl)~~ | System | ✅ DONE | `app/services/fdir_events.py` |
| P0.4 | ~~FDIR diagnostics API (/fdir/ladder, /fdir/events, /fdir/mode)~~ | System | ✅ DONE | `app/routes/fdir.py` |
| P0.5 | ~~Deep healthz endpoint (Janus + stream + mode check)~~ | System | ✅ DONE | `app/routes/system.py` |
| P0.6 | ~~Depth Semantic Contract document~~ | Pipeline | ✅ DONE | `DEPTH_SEMANTIC_CONTRACT.md` |
| P0.7 | ~~Version-control all Janus configs~~ | Infra | ✅ DONE | `infrastructure/{depth,color}_node/janus/` |
| P0.8 | ~~Enable hardware watchdog (`RuntimeWatchdogSec=30`) on both nodes~~ | System | ✅ DONE | `system.conf.d/watchdog.conf` on both Pi |
| P0.9 | ~~Add `dtls_mtu=1200` to both Janus configs~~ | Janus | ✅ DONE | `janus.jcfg` media block |
| P0.10 | ~~Add `slowlink_threshold=50` to both Janus configs~~ | Janus | ✅ DONE | `janus.jcfg` media block |
| P0.11 | ~~Add `ice_ignore_list` `tailscale` to color node Janus~~ | Janus | ✅ DONE | color `janus.jcfg` NAT block |
| P0.12 | ~~Enable Janus Admin API on color node (with auth secret)~~ | Janus | ✅ DONE | color `janus.transport.http.jcfg` port 7088 |
| P0.13 | ~~Set admin secret on depth node Admin API~~ | Janus | ✅ DONE | depth `janus.jcfg` general block |
| P0.14 | ~~Version-control all systemd units from both nodes~~ | Infra | ✅ DONE | `infrastructure/*/systemd/` |

### P1 — Security & network reliability (do next)

| # | Task | Domain | Status | Where |
|---|------|--------|--------|-------|
| P1.1 | ~~Remove hardcoded TURN password from settings.py~~ | Security | ✅ DONE | `app/core/settings.py` |
| P1.2 | ~~Remove hardcoded TURN password from JanusNatConfig~~ | Security | ✅ DONE | `app/routes/janus.py` |
| P1.3 | ~~Fix admin check bypass (enforce via env flag)~~ | Security | ✅ DONE | `app/core/admin.py` |
| P1.4 | ~~Move TURN_PASS to systemd EnvironmentFile~~ | Security | ✅ DONE | `/etc/robot/camera-secrets.env` + override.conf |
| P1.5 | ~~Implement TURN REST API ephemeral credentials~~ | TURN | ✅ DONE | `app/routes/janus.py` `generate_turn_credentials()` |
| P1.6 | ~~Configure TURN/TLS on port 443 for hostile networks~~ | TURN | ✅ DONE | `infrastructure/vps_turn/turnserver.conf` + UFW 443 |
| P1.7 | ~~Audit coturn on VPS: denied-peer-ip, no-multicast, stale-nonce~~ | TURN | ✅ DONE | VPS turnserver.conf hardened |
| P1.8 | ~~Set up iptables on both Pi: allow only needed ports~~ | Network | ✅ DONE | `infrastructure/*/firewall-*.sh` |
| P1.9 | Move color camera to USB 3.0 port | Sensor | TODO | Physical hardware change |
| P1.10 | ~~Fix `nat_1_1_mapping` — dynamic IP auto-update cron~~ | Network | ✅ DONE | `update-nat-mapping.sh` (*/15 cron) |
| P1.11 | ~~Multi-TURN failover (UDP+TCP+TLS transports)~~ | TURN | ✅ DONE | `/client-config` multi-URL |

### P2 — Observability & verification (ongoing)

| # | Task | Domain | Status | Where |
|---|------|--------|--------|-------|
| P2.1 | ~~Define SLOs: ICE <5s, TTFF <8s, MTTR <60s, avail ≥99%~~ | System | ✅ DONE | `SLO.md` |
| P2.2 | ~~Prometheus metrics export (watchdog, mode, FDIR, ladder)~~ | Observability | ✅ DONE | `app/routes/metrics.py` + instrumentation |
| P2.3 | ~~Client getStats() → server telemetry pipeline~~ | Client | ✅ DONE | `telemetry.js` + `app/routes/telemetry.py` |
| P2.4 | ~~Persistent journald (Storage=persistent, 200M cap)~~ | System | ✅ DONE | `journald.conf.d/persistent.conf` on both Pi |
| P2.5 | ~~Automated off-nominal drill harness~~ | Testing | ✅ DONE | `tests/drill_harness.py` |
| P2.6 | ~~CPU/thermal de-rate (lower FPS when hot)~~ | Pipeline | ✅ DONE | `app/services/thermal.py` |
| P2.7 | ~~QoS traffic classification (tc/fq_codel + DSCP)~~ | Network | ✅ DONE | `infrastructure/*/qos-media.sh` |
| P2.8 | Trickle ICE test from each network type | Verification | TODO | Manual + script |
| P2.9 | ~~CSP / frame-ancestors / security headers~~ | Client | ✅ DONE | `SecurityHeadersMiddleware` in `app/core/app.py` |
| P2.10 | ~~Remove SSH password from deploy docs~~ | Security | ✅ DONE | DEPTH_CAMERA_DEPLOY.md, DEPLOY_COLOR_FRAME.md |

### P3 — E2E provability & architecture clarity (v1.6)

| # | Task | Domain | Status | Where |
|---|------|--------|--------|-------|
| P3.1 | ~~Formalize component roles (CF/TURN/Janus/FastAPI/.55)~~ | Architecture | ✅ DONE | Spec §1 "Component Roles" table |
| P3.2 | ~~Cross-layer timeout contract (source of truth)~~ | Contract | ✅ DONE | Spec §9 "Timeout Contract" table |
| P3.3 | ~~Stream-level health endpoint (`/health/stream`)~~ | Observability | ✅ DONE | `app/routes/system.py` |
| P3.4 | ~~TTFF histogram + client loss/frames metrics~~ | Observability | ✅ DONE | `app/routes/metrics.py` + `telemetry.py` |
| P3.5 | ~~Synthetic browser canary (Playwright)~~ | Verification | ✅ DONE | `scripts/browser_canary.py` |
| P3.6 | ~~Grafana dashboards provisioned~~ | Observability | ✅ DONE | `monitoring/grafana-dashboard.json` |
| P3.7 | ~~Alertmanager rules for SLO burn-rate~~ | Observability | ✅ DONE | `monitoring/camstack-alert-rules.yml` |
| P3.8 | ~~External synthetic probe (cron outside LAN)~~ | Verification | ✅ DONE | `scripts/browser_canary.py --http-only --external` |
| P3.9 | ~~Fault injection matrix — automated~~ | Verification | ✅ DONE | `scripts/fault_drills.py` (8 drills) |

---

## Critical Configuration Diffs (found during audit)

### Color node vs Depth node Janus gaps

| Setting | Color (.10) | Depth (.55) | Required | 
|---------|-------------|-------------|----------|
| `no_media_timer` | ✅ 30 (fixed) | ✅ 30 | Both ✅ |
| `rtp_port_range` | ✅ "5002-5120" (fixed) | ✅ "5002-5120" | Both ✅ |
| `server_name` | ✅ "color-node" (fixed) | ✅ "AE-ROBOT-55" | Both ✅ |
| `debug_level` | ✅ 2 (fixed) | ✅ 2 | Both ✅ | 
| `debug_timestamps` | ✅ true (fixed) | ✅ true | Both ✅ |
| Admin API HTTP | ✅ enabled port 7088 (fixed) | ✅ enabled port 7088 | Both ✅ |
| `admin_secret` | ✅ set (fixed) | ✅ set (fixed) | Both ✅ |
| `ice_ignore_list` | ✅ includes `tailscale` (fixed) | ✅ includes `tailscale` | Both ✅ |
| `dtls_mtu` | ✅ 1200 (fixed) | ✅ 1200 (fixed) | Both ✅ |
| `slowlink_threshold` | ✅ 50 (fixed) | ✅ 50 (fixed) | Both ✅ |
| `nat_1_1_mapping` | "87.156.23.54" (⚠️ verify) | ❌ not set | color only |
| HW watchdog | ✅ RuntimeWatchdogSec=30 | ✅ RuntimeWatchdogSec=30 | Both ✅ |

### Streaming plugin port conflict

Color node uses port **5004** for RGB mountpoint (id 1305), but depth node also uses **5004** for the depth mountpoint (id 1306). Since they're separate hosts this is OK, but documentation should clarify.

### Network topology critical path

```
Internet ← wlan0(.10) ← br0(192.168.1.10) ← wlan0(192.168.1.55)
                         ↑ ip_forward=1          ↑ default via .10
                         ↑ gateway/NAT            ↑ depth node
```

- Color node is the **only internet exit** for depth node
- If wlan0 on color node loses uplink → both nodes lose remote access
- Tailscale on depth node provides out-of-band access (good)
- No Tailscale on color node (gap — should add for full OOB coverage)
