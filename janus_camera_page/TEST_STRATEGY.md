# Camera Stack — Test Strategy

> Version: 1.0 | Date: 2026-03-06
> Scope: L1 (Sensors) → L7 (Client) + cross-layer drills X1–X6

---

## 1. Coverage Model

Tests are organized in five escalating tiers.  Each tier builds on the
one below and requires different infrastructure.

| Tier | Scope | Infra needed | Runner | Cadence |
|------|-------|-------------|--------|---------|
| **Unit** | Single function / class in isolation | None (mocks) | `pytest -m "not drill and not soak"` | Every commit |
| **Integration** | Module interactions (FastAPI routing, proxy chain, FDIR state machine) | None (ASGI test client) | `pytest -m integration` | Every commit |
| **Contract** | Data-format invariants against `DEPTH_SEMANTIC_CONTRACT.md` | None (synthetic numpy) | `pytest -m contract` | Every commit |
| **Drill** | Live fault-injection on real Pi nodes via SSH | Two Pi nodes on LAN | `pytest tests/drill_harness.py --node=<ip>` | Pre-release + weekly |
| **Soak** | 8 h / 24 h continuous operation with metric collection | Live deployment | `python tests/soak_runner.py --hours=8` | Nightly (8 h) / release (24 h) |

### Test markers (pytest.ini)

```
unit, integration, slow, hardware, contract, security, drill, soak, cross_layer
```

---

## 2. Layer Responsibility Matrix

Each layer owns a specific failure domain.  Tests for that layer must
validate **only** its own contracts; cross-layer coupling is tested
separately in X1–X6 drills.

| Layer | Owner | Tests must verify | Tests must NOT touch |
|-------|-------|-------------------|----------------------|
| **L1 Sensors** | Hardware (USB, power, thermal) | Camera detect, serial match, USB topology, no undervoltage | Encoding params, Janus mounts, API routes |
| **L2 Capture** | `realsense_mux.py`, V4L2 | Frame shape/dtype/rotation, timestamp monotonicity, FIFO recovery | ffmpeg args, RTP ports, network |
| **L3 Encoding** | ffmpeg / systemd units | RTP packet flow, encoder args match profile, snapshot isolation | Janus internals, API behavior, client JS |
| **L4 Media Broker** | Janus Streaming Plugin | Mount IDs exist, attach/watch/start cycle, session cleanup, admin API | Camera capture, API auth, CORS |
| **L5 Control/API** | FastAPI (.10 + .55) | Route contracts (2xx/4xx/5xx), admin auth, CSP, CORS, proxy correctness | Janus protocol details, USB reset, ffmpeg |
| **L6 Network** | Cloudflare Tunnel + TURN + LAN | External reachability, TURN relay candidates, uplink recovery | Camera frames, Janus mount config |
| **L7 Client** | Browser player + iframe | TTFF, ICE timing, getStats() metrics, reconnect, hidden-tab resume | Server-side FDIR, systemd services |

---

## 3. Conflict Prevention Rules

1. **Single owner per failure mode.**  If Janus dies, L4 tests verify
   mount recovery.  L5 tests verify `/healthz` reports it.  L3 tests
   verify ffmpeg is NOT killed by the Janus restart.

2. **No transitive assertions.**  L5 proxy tests assert HTTP status
   codes, not depth frame content.  L2 contract tests assert frame
   dtype, not HTTP response codes.

3. **Mocks at layer boundary.**  Each unit / integration test mocks the
   layer below.  For example L5 proxy tests mock
   `depth_camera_proxy.forward_request` — they never call the real .55
   node.

4. **Shared state isolation.**  Tests that modify `system_mode._state`
   or `fdir_events._ring` must reset them in teardown.  FDIR
   integration tests get their own `RecoveryLadder()` instance.

5. **No CI dependency on hardware.**  All CI-runnable tests (`unit`,
   `integration`, `contract`, `security`) use only mocks and synthetic
   data.  The `drill` and `soak` markers are excluded from CI.

---

## 4. Test Naming Convention

```
test_{layer}_{type}_{sequence}.py
```

| Pattern | Example | Purpose |
|---------|---------|---------|
| `test_depth_contract.py` | L2 contract | Depth semantic invariants |
| `test_security.py` | L5 security | Auth, CSP, CORS boundary |
| `test_depth_proxy_routes.py` | L5 integration | Proxy route correctness |
| `test_fdir_integration.py` | L4/L5 integration | FDIR ladder + mode transitions |
| `test_layer_isolation.py` | Cross-layer | Verify no cascading failures |
| `test_canary_contract.py` | L7 contract | Browser canary output schema |
| `drill_harness.py` | L1–L7 drill | Live fault-injection on Pi nodes |
| `soak_runner.py` | X6 soak | Long-running metric collection |

---

## 5. SLO Reference (from SLO.md)

| Metric | Target | Alert |
|--------|--------|-------|
| ICE connect (p95) | ≤ 5 s | > 8 s / 5 min |
| TTFF (p95) | ≤ 8 s | > 12 s / 5 min |
| MTTR (p95) | ≤ 60 s | > 120 s / 10 min |
| Stream availability | ≥ 99.0 % | < 97 % / 1 h |
| Packet loss | ≤ 1 % | > 3 % / 5 min |

---

## 6. What to Automate vs Manual

### Automate (CI or nightly)

- API contract tests (all L5 routes)
- Watchdog / FDIR state-machine tests
- Depth semantic contract (L2)
- Player canary schema validation (L7)
- Proxy route coverage (L5)
- Security boundary (CSP, CORS, admin auth)
- Soak smoke with periodic health snapshots (X6)

### Semi-automatic (operator + script)

- Hostile NAT / TURN relay verification (L6)
- Wi-Fi degradation / packet loss injection (L6)
- Brownout / USB fault (L1)
- Thermal stress (L1)
- Full drill harness with SSH (X1–X5)

### Manual

- UX iframe embedding on third-party site (L7)
- Visual depth overlay alignment check (L2)
- Operator assessment after rare hardware faults (L1)

---

## 7. Evidence Requirements

Every test run (automated or manual) must capture the artifacts listed
in `RUNBOOK_EVIDENCE.md`.  A test without evidence is not a test.

---

## 8. Related Documents

| Document | Purpose |
|----------|---------|
| `TEST_MATRIX.md` | All test case IDs L1–L7 + X1–X6 |
| `RELEASE_GATE.md` | Gate A–D pass/fail criteria |
| `FAULT_INJECTION_PLAN.md` | Fault scenarios + expected FDIR behavior |
| `RUNBOOK_EVIDENCE.md` | Artifact collection protocol |
| `SLO.md` | Service level objectives |
| `DEPTH_SEMANTIC_CONTRACT.md` | Breaking change barrier |
| `RELIABILITY_CHECKLIST.md` | Gap analysis + prioritized audit |
