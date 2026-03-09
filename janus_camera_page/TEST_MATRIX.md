# Camera Stack — Test Matrix

> Version: 1.0 | Date: 2026-03-06
> Reference: TEST_STRATEGY.md §2 (Layer Responsibility Matrix)

Status legend: ✅ Automated | 🔧 Semi-auto | 📋 Manual | ❌ Not implemented

---

## L1. Sensors

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L1-SMOKE-01 | smoke | Camera detect after cold boot (model + serial) | 🔧 | ✅ | `scripts/audit_camera_stack.sh` §1 |
| L1-STRESS-02 | stress | 2 h streaming: temp, USB errors, restart count | 🔧 | ❌ | `soak_runner.py` (planned) |
| L1-FAULT-03 | fault | USB disconnect → camera re-enumeration → pipeline recovery | 🔧 | ❌ | `drill_harness.py` (planned) |
| L1-POWER-04 | fault | Voltage sag under full load → no kernel faults | 📋 | ❌ | Manual + `dmesg` |

## L2. Capture

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L2-SMOKE-01 | smoke | 100 consecutive frames: no gaps, no stale timestamps | ✅ | ✅ | `test_depth_contract.py::TestTimestampMonotonicity` |
| L2-CONTRACT-02 | contract | shape, dtype, rotation, timestamp freshness per contract | ✅ | ✅ | `test_depth_contract.py::TestDepthFrameContract` / `TestColorFrameContract` |
| L2-FAULT-03 | fault | Kill FIFO consumer → reopen logic → escalation if limit | ✅ | ❌ | (planned: mock FIFO test) |
| L2-RECOVERY-04 | fault | pyrealsense2 pipeline crash → process restart | 🔧 | ❌ | `drill_harness.py::TestDrill02` (partial) |

## L3. Encoding / Pipelines

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L3-SMOKE-01 | smoke | Encoder processes alive + RTP on localhost | 🔧 | ✅ | `scripts/audit_camera_stack.sh` |
| L3-CONTRACT-02 | contract | Runtime ffmpeg args match reference profile | ✅ | ❌ | (planned) |
| L3-LOAD-03 | stress | All streams + depth poll + snapshot simultaneously | 🔧 | ❌ | `soak_runner.py` (planned) |
| L3-FAULT-04 | fault | Restart one pipeline → neighbors survive | ✅ | ✅ | `test_layer_isolation.py::TestPipelineIsolation` |

## L4. Media Broker (Janus)

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L4-SMOKE-01 | smoke | For each mount: session → attach → watch → start | ✅ | ✅ | `test_janus_service.py::TestCreateSession` / `TestAttachStreaming` |
| L4-OBS-02 | obs | Admin snapshot: ICE/DTLS/media state correct | 🔧 | ❌ | (planned) |
| L4-FAULT-03 | fault | `systemctl restart janus` during active watch | 🔧 | ✅ | `drill_harness.py::TestDrill01_JanusRestart` |
| L4-LEAK-04 | stress | 100 attach/detach cycles → no session/handle accumulation | ✅ | ❌ | (planned: soak_runner session count) |

## L5. Control / API Layer

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L5-API-01 | contract | All GET/POST routes → 2xx/4xx/5xx match spec | ✅ | ✅ | `test_system_routes.py` / `test_camera_routes.py` / `test_janus_routes.py` |
| L5-SEC-02 | security | Admin routes without token → 401/403 | ✅ | ✅ | `test_security.py::TestAdminAuth` |
| L5-IFRAME-03 | security | Embedding on allowed origin OK, disallowed blocked by CSP | ✅ | ✅ | `test_security.py::TestCSPFrameAncestors` |
| L5-PROXY-04 | fault | .55 offline → depth proxy returns 502, .10 stays healthy | ✅ | ✅ | `test_depth_proxy_routes.py::TestUpstreamFailure` |
| L5-CORS-05 | security | CORS allows exact origins, rejects wildcards | ✅ | ✅ | `test_security.py::TestCORS` |

## L6. Network Access

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L6-SMOKE-01 | smoke | Player page reachable via public hostname | 🔧 | ✅ | `browser_canary.py --http-only` |
| L6-TURN-02 | integration | Hostile NAT → client uses relay candidates | 🔧 | ❌ | (planned: P2.8 trickle ICE script) |
| L6-FAULT-03 | fault | Uplink loss 30/60/120 s → controlled degradation → recovery | 🔧 | ✅ | `drill_harness.py::TestDrill08_UplinkFlap` |
| L6-LINK-04 | fault | Controlled loss/jitter .10 ↔ .55 → observable degradation | 🔧 | ❌ | (planned: tc netem drill) |

## L7. Client

| ID | Type | Description | Automation | Status | File / Tool |
|----|------|-------------|-----------|--------|-------------|
| L7-SMOKE-01 | smoke | Player page loads, TTFF within SLO | ✅ | ✅ | `browser_canary.py` / `test_canary_contract.py` |
| L7-METRICS-02 | obs | getStats() after 30 s: inbound-rtp, bytes/frames growing | ✅ | ✅ | `browser_canary.py` metrics extraction |
| L7-RESUME-03 | fault | Hidden tab 1/5/15 min → recovery on return | 🔧 | ❌ | (planned: Playwright hidden-tab drill) |
| L7-EXHAUST-04 | stress | 20 reconnect cycles → no handler/session leaks | ✅ | ❌ | (planned: canary loop script) |

---

## Cross-Layer Tests

| ID | Layers | Description | Automation | Status | File / Tool |
|----|--------|-------------|-----------|--------|-------------|
| X1 | L1–L7 | Cold boot E2E: power → player page → first frame → depth API | 🔧 | ✅ | `drill_harness.py::TestDrill06_ColdBootE2E` |
| X2 | L5–L7 | Hostile NAT E2E: iframe host → CF control-plane → TURN media | 🔧 | ❌ | (planned) |
| X3 | L4–L7 | Janus restart during watch → MTTR within target | 🔧 | ✅ | `drill_harness.py::TestDrill01_JanusRestart` |
| X4 | L1–L5 | Depth node isolation: .55 down → .10 color survives, depth 502 | 🔧 | ✅ | `drill_harness.py::TestDrill07_DepthNodeIsolation` |
| X5 | L5–L6 | Uplink flap: WAN down → local mode → WAN up → auto-recovery | 🔧 | ✅ | `drill_harness.py::TestDrill08_UplinkFlap` |
| X6 | All | Soak 24 h: no restart storm, leak, silent freeze | 🔧 | ❌ | `soak_runner.py` (planned) |

---

## Mapping: Existing Tests → Matrix IDs

| Existing test file | Covers IDs |
|----|------------|
| `test_camera.py` | L5-API-01 (partial) |
| `test_janus_service.py` | L4-SMOKE-01 |
| `test_janus_routes.py` | L5-API-01 (Janus routes) |
| `test_system_routes.py` | L5-API-01 (system routes) |
| `test_camera_routes.py` | L5-API-01 (camera routes) |
| `test_proxies.py` | L5-PROXY-04 (partial) |
| `test_watchdogs.py` | L4-FAULT-03 (unit-level) |
| `test_v4l2_service.py` | L2-CONTRACT-02 (partial — V4L2 modes only) |
| `test_system_service.py` | L5-API-01 (systemd wrappers) |
| `test_env_store.py` | L5-API-01 (env file CRUD) |
| `drill_harness.py` | X3, L4-FAULT-03, L3-FAULT-04 (via drill 02) |
| `scripts/audit_camera_stack.sh` | L1-SMOKE-01, L3-SMOKE-01 |
| `scripts/browser_canary.py` | L6-SMOKE-01, L7-SMOKE-01, L7-METRICS-02 |

---

## Coverage Summary

| Layer | Total cases | Automated | Semi-auto | Manual | Gap |
|-------|------------|-----------|-----------|--------|-----|
| L1 | 4 | 0 | 1 | 1 | 2 |
| L2 | 4 | 2 | 0 | 0 | 2 |
| L3 | 4 | 1 | 1 | 0 | 2 |
| L4 | 4 | 1 | 1 | 0 | 2 |
| L5 | 5 | 5 | 0 | 0 | 0 |
| L6 | 4 | 0 | 2 | 0 | 2 |
| L7 | 4 | 2 | 0 | 0 | 2 |
| X1–X6 | 6 | 0 | 4 | 0 | 2 |
| **Total** | **35** | **11** | **9** | **1** | **14** |
