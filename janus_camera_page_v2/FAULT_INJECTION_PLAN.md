# Camera Stack — Fault Injection Plan

> Version: 1.0 | Date: 2026-03-06
> Reference: RELEASE_GATE.md (Gate C), recovery_ladder.py, system_mode.py

---

## 1. FDIR Ladder Reference

| Level | Name | Action | Max attempts | Cooldown |
|-------|------|--------|-------------|----------|
| 0 | retry_handle | Verify Janus + pipeline status | 1 | 10 s |
| 1 | restart_pipeline | `systemctl restart <service>` | 5 | 45 s |
| 2 | restart_janus | `systemctl restart janus.service` | 3 | 90 s |
| 3 | usb_reset | Hardware reset (depth only) | 2 | 90 s |
| 4 | reboot_node | `systemctl reboot` (bounded) | 1 | 300 s |

Circuit breaker: after `MAX_FDIR_REBOOTS` (default 2) FDIR-initiated
reboots → SAFE mode, no more reboots.

## 2. System Modes

| Mode | Streams | FPS cap | Require TURN | Require uplink |
|------|---------|---------|-------------|----------------|
| NOMINAL | ✅ | 30 | ✅ | ✅ |
| DEGRADED | ✅ | 15 | ✅ | ✅ |
| LOCAL_ONLY | ✅ | 15 | ❌ | ❌ |
| SAFE | ❌ | 0 | ❌ | ❌ |

Degradation is monotonic: each ladder escalation calls `degrade()`.
Promotion back to NOMINAL requires explicit `promote()` after healthy streak.

---

## 3. Fault Scenarios

### F01 — Janus process death

| Field | Value |
|-------|-------|
| Injection | `systemctl stop janus.service` or `kill -9 $(pidof janus)` |
| Detection | Watchdog: `video_age_ms > 10000` (stale stream) |
| Expected FDIR | Level 0 retry → Level 2 restart_janus |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Janus restarts, mount re-appears, stream resumes |
| MTTR target | ≤ 60 s |
| drill_harness | `TestDrill01_JanusRestart` |

### F02 — ffmpeg pipeline crash

| Field | Value |
|-------|-------|
| Injection | `pkill -9 ffmpeg` |
| Detection | Watchdog: stale `video_age_ms` (no RTP packets) |
| Expected FDIR | Level 0 retry → Level 1 restart_pipeline |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Pipeline systemd unit restarts, RTP resumes |
| MTTR target | ≤ 45 s |
| drill_harness | `TestDrill02_PipelineRestart` |

### F03 — TURN port block (network blip)

| Field | Value |
|-------|-------|
| Injection | `iptables -I OUTPUT -p udp --dport 3478 -j DROP` for 15 s |
| Detection | Client: ICE disconnected → reconnect attempt |
| Expected FDIR | No server-side FDIR (client reconnects) |
| Expected mode | Unchanged (stream still flows on LAN) |
| Recovery | After iptables rule removed, TURN allocations resume |
| MTTR target | ≤ 30 s after rule removed |
| drill_harness | `TestDrill03_NetworkBlip` |

### F04 — Full service restart

| Field | Value |
|-------|-------|
| Injection | `systemctl restart janus-camera-page.service` |
| Detection | Health endpoint temporarily unreachable |
| Expected FDIR | None (graceful restart) |
| Expected mode | NOMINAL (after restart) |
| Recovery | sd_notify signals readiness, watchdog resets |
| MTTR target | ≤ 15 s |
| drill_harness | `TestDrill04_FullServiceRestart` |

### F05 — realsense_mux.py crash (depth node)

| Field | Value |
|-------|-------|
| Injection | `pkill -9 -f realsense_mux.py` on .55 |
| Detection | FIFO broken pipe → systemd restart |
| Expected FDIR | systemd Restart=on-failure (not ladder — separate unit) |
| Expected mode | .55 briefly DEGRADED, recovers to NOMINAL |
| Recovery | Process restarts, pipeline re-opens FIFOs |
| MTTR target | ≤ 30 s |
| drill_harness | `TestDrill02` (on depth node) |

### F06 — Cold boot E2E

| Field | Value |
|-------|-------|
| Injection | Power cycle both nodes |
| Detection | Operator observes boot sequence |
| Expected FDIR | None (normal startup) |
| Expected mode | NOMINAL within 120 s of power-on |
| Recovery | N/A (initial boot) |
| gate | Gate A (all checks) |
| drill_harness | `TestDrill06_ColdBootE2E` |

### F07 — Depth node isolation (.55 unreachable from .10)

| Field | Value |
|-------|-------|
| Injection | `iptables -I INPUT -s 192.168.1.55 -j DROP` on .10 |
| Detection | Depth proxy timeouts, `/healthz` reports depth_camera: unreachable |
| Expected FDIR | .10 system enters DEGRADED |
| Expected mode | DEGRADED (color survives, depth routes → 502) |
| Recovery | Remove iptables rule → depth proxy recovers |
| MTTR target | ≤ 30 s after link restore |
| drill_harness | `TestDrill07_DepthNodeIsolation` |

### F08 — WAN uplink flap

| Field | Value |
|-------|-------|
| Injection | `ip link set wlan0 down` on .10 for 30/60/120 s |
| Detection | Cloudflare tunnel down, TURN unreachable |
| Expected FDIR | System transitions to LOCAL_ONLY |
| Expected mode | LOCAL_ONLY while uplink is down |
| Recovery | `ip link set wlan0 up` → tunnel reconnects, promote to NOMINAL |
| MTTR target | ≤ 60 s after uplink returns |
| drill_harness | `TestDrill08_UplinkFlap` |

### F09 — Dual fault (Janus + pipeline simultaneously)

| Field | Value |
|-------|-------|
| Injection | `kill -9 $(pidof janus) && pkill -9 ffmpeg` |
| Detection | Watchdog: both stale video and Janus unreachable |
| Expected FDIR | Level 0 retry (fail) → Level 1 restart_pipeline → Level 2 restart_janus |
| Expected mode | NOMINAL → DEGRADED |
| Recovery | Both services restart via ladder |
| MTTR target | ≤ 90 s |
| drill_harness | `TestDrill09_DualFault` |

### F10 — Depth proxy failover

| Field | Value |
|-------|-------|
| Injection | Stop FastAPI on .55 (`systemctl stop janus-camera-page` on depth) |
| Detection | Proxy connect error → HTTP 502 |
| Expected FDIR | No .10 FDIR escalation (proxy returns clean error) |
| Expected mode | DEGRADED on .10 (depth subsystem down) |
| Recovery | Restart service on .55 → proxy resumes |
| MTTR target | ≤ 15 s after .55 service restarts |
| drill_harness | `TestDrill10_DepthProxyFailover` |

### F11 — Reboot circuit breaker

| Field | Value |
|-------|-------|
| Injection | Artificially set reboot count ≥ `MAX_FDIR_REBOOTS` |
| Detection | Ladder reaches level 4, reads reboot count |
| Expected FDIR | Circuit breaker trips → SAFE mode (no reboot) |
| Expected mode | SAFE |
| Recovery | Manual reset required (`/fdir/ladder/reset`) |
| test | `test_fdir_integration.py::TestCircuitBreaker` |

---

## 4. Fault Injection Safety Rules

1. **Never inject faults in production without a maintenance window.**
2. **Always have SSH access to both nodes before starting drills.**
3. **Record the exact injection command and timestamp.**
4. **Set a timer for max duration of any iptables/link-down injection.**
5. **Verify /healthz returns 200 before AND after every drill.**
6. **If a drill leaves the system in SAFE mode, manually reset via
   `/fdir/ladder/reset` and `/fdir/mode/nominal`.**

---

## 5. Automation Status

| Scenario | drill_harness.py | Unit test | Status |
|----------|-----------------|-----------|--------|
| F01 Janus death | `TestDrill01` | `test_watchdogs.py` | ✅ |
| F02 Pipeline crash | `TestDrill02` | `test_watchdogs.py` | ✅ |
| F03 TURN block | `TestDrill03` | — | ✅ |
| F04 Service restart | `TestDrill04` | — | ✅ |
| F05 realsense_mux crash | `TestDrill02` (depth) | — | ✅ |
| F06 Cold boot | `TestDrill06` | — | ✅ (new) |
| F07 Depth isolation | `TestDrill07` | `test_layer_isolation.py` | ✅ (new) |
| F08 Uplink flap | `TestDrill08` | — | ✅ (new) |
| F09 Dual fault | `TestDrill09` | — | ✅ (new) |
| F10 Proxy failover | `TestDrill10` | `test_depth_proxy_routes.py` | ✅ (new) |
| F11 Circuit breaker | — | `test_fdir_integration.py` | ✅ (new) |
