# Camera Stack — Release Gate Criteria

> Version: 1.0 | Date: 2026-03-06
> Owner: Camera team
> Reference: SLO.md, TEST_MATRIX.md

A release is blocked until all four gates pass.  Each gate has explicit
PASS/FAIL criteria and required evidence artifacts.

---

## Gate A — Boot & Basic Operation

**Purpose:** Prove that a cold start produces a fully operational stack
without any human intervention.

| # | Check | PASS criterion | Evidence |
|---|-------|---------------|----------|
| A.1 | Cold start .10 and .55 | Both nodes boot to login, all `systemd` units active | `systemctl list-units --state=failed` = empty |
| A.2 | Camera detection | `.10` → D435i, `.55` → D435, serials match inventory | `audit_camera_stack.sh` output §1 |
| A.3 | Janus mounts | All configured mount IDs present in Janus `/info` | `curl localhost:8088/janus/info` |
| A.4 | Local video | Color stream visible on LAN via `http://192.168.1.10:8900/` | Screenshot + browser console clean |
| A.5 | Depth API | `GET /depth?x=50&y=50` returns valid float | `curl` response body |
| A.6 | Health endpoint | `/healthz` → 200, `ok: true`, `mode: nominal` | Captured JSON |

**Gate A verdict:** PASS = all 6 checks pass. Any FAIL → release blocked.

---

## Gate B — External Operation

**Purpose:** Prove end-to-end access from outside the LAN through
Cloudflare Tunnel + TURN.

| # | Check | PASS criterion | Evidence |
|---|-------|---------------|----------|
| B.1 | External page load | `https://api.techvisioncloud.pl/` returns player HTML | HTTP 200 + page title |
| B.2 | Client config | `/client-config` returns ICE servers with TURN entries | JSON response |
| B.3 | WebRTC via TURN | Hostile NAT client receives video (relay candidates used) | `getStats()` dump showing relay candidate type |
| B.4 | iframe embedding | Player loads inside iframe on `*.techvisioncloud.pl` | Screenshot from iframe host |
| B.5 | iframe rejection | Player blocked in iframe on unauthorized origin | Browser console showing CSP violation |

**Gate B verdict:** PASS = all 5 checks pass.

---

## Gate C — Recovery

**Purpose:** Prove that the FDIR ladder handles all standard fault
scenarios within MTTR budget (≤ 60 s).

| # | Fault scenario | PASS criterion | Evidence |
|---|---------------|---------------|----------|
| C.1 | Janus restart (`.10`) | Stream recovers within 60 s, no manual reload | drill_harness `TestDrill01` result |
| C.2 | ffmpeg pipeline kill | Watchdog detects stale stream, ladder restarts pipeline | drill_harness `TestDrill02` result |
| C.3 | `realsense_mux.py` crash (`.55`) | systemd restarts process, depth stream resumes | `systemctl status` + `/healthz` |
| C.4 | Uplink loss (30 s) | System enters LOCAL_ONLY, recovers on uplink return | drill_harness `TestDrill08` or manual |
| C.5 | Hidden tab (5 min) → resume | Player reconnects, no stale callbacks | Browser console + video playing |
| C.6 | .55 isolation from .10 | `.10` color survives, depth routes → 502, recovery after link restore | drill_harness `TestDrill07` |

**Gate C verdict:** PASS = all 6 checks pass. Degraded (but functional)
recovery counts as PASS if MTTR < 60 s.

---

## Gate D — Soak

**Purpose:** Prove sustained reliability over extended operation.

| # | Check | PASS criterion | Evidence |
|---|-------|---------------|----------|
| D.1 | 8 h minimum soak | No restart storm, no memory leak, no silent freeze | `soak_runner.py` JSON report |
| D.2 | Memory stability | RSS growth ≤ 50 MB over 8 h | soak report memory timeline |
| D.3 | Session leaks | Janus handle count stable (±5 over 8 h) | soak report handle count |
| D.4 | Metrics continuity | `camstack_stream_active == 1` for ≥ 99 % of soak | soak report uptime fraction |
| D.5 | 24 h qualification (recommended) | Same as D.1–D.4 over 24 h | Extended soak report |

**Gate D verdict:** PASS = D.1–D.4 pass. D.5 is recommended for major releases.

---

## FAIL Criteria (any gate)

A test is **FAIL** if any of the following occurs:

- Manual restart needed where auto-recovery was expected
- `/healthz` reports healthy but first frame never arrives
- Zombie sessions / handles / listeners remain after recovery
- Iframe embedding works on an unauthorized origin
- Admin route opens without authentication
- Depth semantic contract violated (shape, dtype, rotation)
- Restart storm detected (> 3 restarts in 5 min)
- Insufficient evidence artifacts for root cause analysis

---

## Evidence Checklist (per gate run)

See `RUNBOOK_EVIDENCE.md` for the full artifact list.  Minimum per gate:

- [ ] Timestamp + operator name
- [ ] Git commit SHA
- [ ] `systemctl list-units --state=failed` on both nodes
- [ ] Last 200 lines of `journalctl -u janus-camera-page` on both nodes
- [ ] `/healthz`, `/status`, `/metrics` responses
- [ ] Janus `/info` + admin session count
- [ ] Browser `getStats()` dump (for Gate B/D)
- [ ] Final verdict: **PASS** / **FAIL** / **DEGRADED**
- [ ] Root cause if FAIL
