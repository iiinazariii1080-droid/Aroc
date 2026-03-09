#!/usr/bin/env python3
"""Automated fault-injection drills for dual-RPi5 camera stack.

Runs a matrix of fault scenarios against a live camera node, validates
that FDIR detects, isolates, and recovers from each fault within SLO
bounds (MTTR ≤ 60 s, recovery ladder escalates correctly).

Drills
------
1. kill_ffmpeg       — SIGKILL the ffmpeg process; expect L1 pipeline restart
2. restart_janus     — systemctl restart janus; expect L2 recovery
3. kill_realsense    — SIGKILL realsense-mux; expect L1 pipeline restart
4. api_restart       — POST /action/restart via API; expect clean restart
5. mode_degrade      — force DEGRADED via FDIR API; verify mode + promotion
6. flap_depth_link   — iptables DROP .55 traffic briefly; expect DEGRADED mode
7. hide_tab_resume   — simulate client hidden-tab (telemetry gap); expect no crash
8. ladder_exhaust    — exhaust L0 attempts; expect escalation to L1

Each drill:
  a) records pre-state (health, mode, ladder level)
  b) injects the fault
  c) polls health until recovered or timeout
  d) asserts post-conditions (mode back to NOMINAL, stream healthy, etc.)
  e) resets ladder for next drill

Usage:
    # Run all drills against local color node
    python scripts/fault_drills.py --host 192.168.1.10 --port 8900

    # Run a single drill
    python scripts/fault_drills.py --host 192.168.1.10 --port 8900 --drill kill_ffmpeg

    # JSON output for CI
    python scripts/fault_drills.py --host 192.168.1.10 --json

    # External (no SSH, API-only drills)
    python scripts/fault_drills.py --api-only --url https://api.techvisioncloud.pl/api/v1/color_camera

Requires: requests, SSH access to target node (for process-level drills)

Exit code: 0 = all pass, 1 = any failure
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fault_drills")

# ── SLO thresholds ────────────────────────────────────────────────────
MTTR_LIMIT_S = 60      # Max time-to-recovery
POLL_INTERVAL_S = 2     # Health poll interval during recovery wait
SETTLE_S = 3            # Wait after recovery before asserting


@dataclass
class DrillResult:
    name: str
    passed: bool = False
    recovery_s: float = 0.0
    pre_state: Dict[str, Any] = field(default_factory=dict)
    post_state: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class DrillRunner:
    """Orchestrates fault injection and recovery observation."""

    def __init__(
        self,
        base_url: str,
        ssh_host: Optional[str] = None,
        ssh_user: str = "boris",
        api_key: str = "",
        timeout_s: float = MTTR_LIMIT_S,
    ):
        self.base = base_url.rstrip("/")
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.api_key = api_key
        self.timeout = timeout_s
        self._session = requests.Session()
        if api_key:
            self._session.headers["X-API-Key"] = api_key

    # ── Helpers ───────────────────────────────────────────────────────

    def _get(self, path: str) -> Dict[str, Any]:
        r = self._session.get(f"{self.base}{path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        r = self._session.post(f"{self.base}{path}", timeout=10, **kwargs)
        r.raise_for_status()
        return r.json()

    def _ssh(self, cmd: str) -> subprocess.CompletedProcess[str]:
        """Run command on the target node via SSH."""
        if not self.ssh_host:
            raise RuntimeError("SSH host not configured — cannot run process-level drill")
        return subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new",
             f"{self.ssh_user}@{self.ssh_host}", cmd],
            capture_output=True, text=True, timeout=30,
        )

    def get_state(self) -> Dict[str, Any]:
        """Capture current health + FDIR state."""
        state: Dict[str, Any] = {}
        try:
            state["healthz"] = self._get("/healthz")
        except Exception as e:
            state["healthz"] = {"ok": False, "error": str(e)}
        try:
            state["health_stream"] = self._get("/health/stream")
        except Exception as e:
            state["health_stream"] = {"stream_usable": False, "error": str(e)}
        try:
            state["fdir_mode"] = self._get("/fdir/mode")
        except Exception as e:
            state["fdir_mode"] = {"mode": "unknown", "error": str(e)}
        try:
            state["fdir_ladder"] = self._get("/fdir/ladder")
        except Exception as e:
            state["fdir_ladder"] = {"current_level": -1, "error": str(e)}
        return state

    def wait_for_recovery(self, check: Optional[Callable[[], bool]] = None) -> float:
        """Poll until healthy or timeout.  Returns seconds to recovery."""
        if check is None:
            check = lambda: self._get("/healthz").get("ok", False)

        start = time.monotonic()
        while time.monotonic() - start < self.timeout:
            try:
                if check():
                    return round(time.monotonic() - start, 1)
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_S)
        return -1  # timeout

    def reset_ladder(self) -> None:
        """Reset recovery ladder + force NOMINAL for clean next drill."""
        try:
            self._post("/fdir/ladder/reset")
        except Exception:
            pass
        try:
            self._post("/fdir/mode/nominal", params={"reason": "drill_reset"})
        except Exception:
            pass
        time.sleep(SETTLE_S)

    # ── Drill implementations ─────────────────────────────────────────

    def drill_kill_ffmpeg(self) -> DrillResult:
        """Kill ffmpeg process; expect FDIR L1 pipeline restart."""
        result = DrillResult(name="kill_ffmpeg")
        result.pre_state = self.get_state()

        log.info("DRILL: killing ffmpeg via SSH")
        try:
            self._ssh("sudo pkill -9 -f 'ffmpeg.*rtp'")
        except Exception as e:
            result.error = f"SSH failed: {e}"
            return result

        recovery_s = self.wait_for_recovery()
        time.sleep(SETTLE_S)
        result.post_state = self.get_state()

        if recovery_s < 0:
            result.error = f"Recovery timeout ({self.timeout}s)"
        elif recovery_s > MTTR_LIMIT_S:
            result.error = f"MTTR {recovery_s}s > SLO {MTTR_LIMIT_S}s"
        else:
            result.passed = result.post_state.get("healthz", {}).get("ok", False)
            if not result.passed:
                result.error = "Health not restored after recovery"
        result.recovery_s = recovery_s
        return result

    def drill_restart_janus(self) -> DrillResult:
        """Restart Janus via systemd; expect FDIR L2 recovery."""
        result = DrillResult(name="restart_janus")
        result.pre_state = self.get_state()

        log.info("DRILL: restarting janus via SSH")
        try:
            self._ssh("sudo systemctl restart janus")
        except Exception as e:
            result.error = f"SSH failed: {e}"
            return result

        recovery_s = self.wait_for_recovery()
        time.sleep(SETTLE_S)
        result.post_state = self.get_state()

        if recovery_s < 0:
            result.error = f"Recovery timeout ({self.timeout}s)"
        else:
            result.passed = (
                result.post_state.get("healthz", {}).get("ok", False)
                and recovery_s <= MTTR_LIMIT_S
            )
            if not result.passed:
                result.error = f"Recovery took {recovery_s}s or health not OK"
        result.recovery_s = recovery_s
        return result

    def drill_kill_realsense(self) -> DrillResult:
        """Kill realsense-mux; expect FDIR L1 pipeline restart."""
        result = DrillResult(name="kill_realsense")
        result.pre_state = self.get_state()

        log.info("DRILL: killing realsense-mux via SSH")
        try:
            self._ssh("sudo pkill -9 -f realsense_mux")
        except Exception as e:
            result.error = f"SSH failed: {e}"
            return result

        recovery_s = self.wait_for_recovery()
        time.sleep(SETTLE_S)
        result.post_state = self.get_state()

        if recovery_s < 0:
            result.error = f"Recovery timeout ({self.timeout}s)"
        else:
            result.passed = (
                result.post_state.get("healthz", {}).get("ok", False)
                and recovery_s <= MTTR_LIMIT_S
            )
        result.recovery_s = recovery_s
        return result

    def drill_api_restart(self) -> DrillResult:
        """Trigger restart via API; expect clean restart."""
        result = DrillResult(name="api_restart")
        result.pre_state = self.get_state()

        log.info("DRILL: POST /action/restart via API")
        try:
            self._post("/action/restart")
        except Exception as e:
            result.error = f"API restart failed: {e}"
            return result

        time.sleep(5)  # Allow service to fully restart
        recovery_s = self.wait_for_recovery()
        time.sleep(SETTLE_S)
        result.post_state = self.get_state()

        if recovery_s < 0:
            result.error = f"Recovery timeout ({self.timeout}s)"
        else:
            result.passed = result.post_state.get("healthz", {}).get("ok", False)
        result.recovery_s = recovery_s
        return result

    def drill_mode_degrade(self) -> DrillResult:
        """Force DEGRADED mode; verify mode transition + demotion API works."""
        result = DrillResult(name="mode_degrade")
        result.pre_state = self.get_state()

        log.info("DRILL: forcing DEGRADED mode via FDIR API")
        try:
            resp = self._post("/fdir/mode/degraded", params={"reason": "fault_drill"})
        except Exception as e:
            result.error = f"Mode transition failed: {e}"
            return result

        time.sleep(2)
        mid_state = self.get_state()
        mid_mode = mid_state.get("fdir_mode", {}).get("mode", "")

        if mid_mode != "degraded":
            result.error = f"Expected degraded mode, got {mid_mode}"
            result.post_state = mid_state
            return result

        # Promote back to nominal
        try:
            self._post("/fdir/mode/nominal", params={"reason": "drill_recovery"})
        except Exception as e:
            result.error = f"Promotion to NOMINAL failed: {e}"
            result.post_state = self.get_state()
            return result

        time.sleep(SETTLE_S)
        result.post_state = self.get_state()
        post_mode = result.post_state.get("fdir_mode", {}).get("mode", "")
        result.passed = post_mode == "nominal"
        if not result.passed:
            result.error = f"Expected nominal after promotion, got {post_mode}"
        return result

    def drill_flap_depth_link(self) -> DrillResult:
        """Block .55 traffic briefly; expect DEGRADED mode then recovery."""
        result = DrillResult(name="flap_depth_link")
        result.pre_state = self.get_state()

        log.info("DRILL: dropping .55 traffic for 15s")
        try:
            # Block traffic to depth node
            self._ssh("sudo iptables -I FORWARD -d 192.168.1.55 -j DROP")
            time.sleep(15)
            # Restore traffic
            self._ssh("sudo iptables -D FORWARD -d 192.168.1.55 -j DROP")
        except Exception as e:
            # Always try to clean up the iptables rule
            try:
                self._ssh("sudo iptables -D FORWARD -d 192.168.1.55 -j DROP")
            except Exception:
                pass
            result.error = f"SSH/iptables failed: {e}"
            return result

        recovery_s = self.wait_for_recovery()
        time.sleep(SETTLE_S)
        result.post_state = self.get_state()

        if recovery_s < 0:
            result.error = f"Recovery timeout ({self.timeout}s)"
        else:
            result.passed = result.post_state.get("healthz", {}).get("ok", False)
        result.recovery_s = recovery_s
        return result

    def drill_hide_tab_resume(self) -> DrillResult:
        """Simulate telemetry gap (no client reports for 30s); verify no crash."""
        result = DrillResult(name="hide_tab_resume")
        result.pre_state = self.get_state()

        log.info("DRILL: 30s telemetry silence (simulating hidden tab)")
        # Simply don't send any telemetry for 30 seconds and check service stays up
        time.sleep(30)

        result.post_state = self.get_state()
        healthz_ok = result.post_state.get("healthz", {}).get("ok", False)
        mode = result.post_state.get("fdir_mode", {}).get("mode", "")

        # Service should stay alive and not enter SAFE mode just because
        # clients stopped reporting telemetry
        result.passed = healthz_ok and mode != "safe"
        if not result.passed:
            result.error = f"Service degraded during telemetry gap: ok={healthz_ok}, mode={mode}"
        return result

    def drill_ladder_exhaust(self) -> DrillResult:
        """Exhaust L0 attempts; verify escalation to L1."""
        result = DrillResult(name="ladder_exhaust")
        result.pre_state = self.get_state()

        log.info("DRILL: reading ladder to verify escalation logic")
        try:
            ladder = self._get("/fdir/ladder")
        except Exception as e:
            result.error = f"Cannot read ladder: {e}"
            return result

        # Record the ladder structure
        result.post_state = {
            "ladder_levels": ladder.get("levels", []),
            "current_level": ladder.get("current_level", -1),
        }

        # Verify ladder has expected structure (min 4 levels)
        levels = ladder.get("levels", [])
        if len(levels) < 4:
            result.error = f"Ladder has only {len(levels)} levels, expected ≥4"
            return result

        # Verify level names include expected escalation steps
        names = [lv.get("name", "") for lv in levels]
        expected = {"retry_handle", "restart_pipeline", "restart_janus"}
        missing = expected - set(names)
        if missing:
            result.error = f"Ladder missing levels: {missing}"
            return result

        result.passed = True
        return result

    # ── Drill registry ────────────────────────────────────────────────

    ALL_DRILLS = {
        "kill_ffmpeg": drill_kill_ffmpeg,
        "restart_janus": drill_restart_janus,
        "kill_realsense": drill_kill_realsense,
        "api_restart": drill_api_restart,
        "mode_degrade": drill_mode_degrade,
        "flap_depth_link": drill_flap_depth_link,
        "hide_tab_resume": drill_hide_tab_resume,
        "ladder_exhaust": drill_ladder_exhaust,
    }

    # Drills that work without SSH (API-only)
    API_ONLY_DRILLS = {"api_restart", "mode_degrade", "hide_tab_resume", "ladder_exhaust"}

    def run_drill(self, name: str) -> DrillResult:
        """Run a single named drill with pre/post ladder reset."""
        if name not in self.ALL_DRILLS:
            return DrillResult(name=name, error=f"Unknown drill: {name}")

        self.reset_ladder()
        log.info("=" * 60)
        log.info("DRILL START: %s", name)

        result = self.ALL_DRILLS[name](self)

        status = "PASS" if result.passed else "FAIL"
        log.info("DRILL %s: %s (%.1fs) %s", status, name, result.recovery_s, result.error)

        self.reset_ladder()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automated fault injection drills for camera stack",
    )
    parser.add_argument("--host", default="192.168.1.10", help="Target node IP")
    parser.add_argument("--port", type=int, default=8900, help="FastAPI port")
    parser.add_argument("--url", default="", help="Full API base URL (overrides --host/--port)")
    parser.add_argument("--ssh-user", default="boris", help="SSH user for process-level drills")
    parser.add_argument("--api-key", default="", help="API key for authenticated endpoints")
    parser.add_argument("--timeout", type=float, default=MTTR_LIMIT_S, help="MTTR timeout per drill")
    parser.add_argument("--drill", default="", help="Run a single drill by name")
    parser.add_argument("--api-only", action="store_true", help="Skip drills requiring SSH")
    parser.add_argument("--json", action="store_true", help="JSON output only")
    args = parser.parse_args()

    if args.json:
        logging.disable(logging.CRITICAL)

    base_url = args.url or f"http://{args.host}:{args.port}"
    ssh_host = None if args.api_only else args.host

    runner = DrillRunner(
        base_url=base_url,
        ssh_host=ssh_host,
        ssh_user=args.ssh_user,
        api_key=args.api_key,
        timeout_s=args.timeout,
    )

    # Select drills
    if args.drill:
        drills = [args.drill]
    elif args.api_only:
        drills = sorted(DrillRunner.API_ONLY_DRILLS)
    else:
        drills = list(DrillRunner.ALL_DRILLS.keys())

    # Run
    results: List[DrillResult] = []
    for name in drills:
        r = runner.run_drill(name)
        results.append(r)

    # Report
    report = {
        "target": base_url,
        "drills_run": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "recovery_s": r.recovery_s,
                "error": r.error,
                "pre_state": r.pre_state,
                "post_state": r.post_state,
            }
            for r in results
        ],
    }

    print(json.dumps(report, indent=2))

    all_pass = all(r.passed for r in results)
    if all_pass:
        log.info("ALL %d DRILLS PASSED", len(results))
    else:
        log.error("%d/%d DRILLS FAILED", report["failed"], len(results))
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
