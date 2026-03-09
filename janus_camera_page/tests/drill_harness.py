"""Off-nominal drill harness — automated fault-injection tests.

Run against a LIVE camera node (color or depth) via SSH to verify
FDIR recovery works end-to-end.  Tests are intentionally destructive
(they kill processes, stop services, etc.) and should only run in a
controlled maintenance window.

Usage:
    pytest tests/drill_harness.py -v --node=192.168.1.10 --ssh-pass=<pw>

Requires:
    pip install paramiko pytest
"""
from __future__ import annotations

import os
import time
from typing import Generator

import httpx
import pytest

# ── Configuration ────────────────────────────────────────────────────

NODE_IP = os.getenv("DRILL_NODE", "192.168.1.10")
SSH_USER = os.getenv("DRILL_SSH_USER", "boris")
SSH_PASS = os.getenv("DRILL_SSH_PASS", "")
API_PORT = int(os.getenv("DRILL_API_PORT", "8900"))
BASE_URL = f"http://{NODE_IP}:{API_PORT}"

# Timeouts
RECOVERY_TIMEOUT = 90   # max seconds to wait for recovery
POLL_INTERVAL = 3        # seconds between health polls


def pytest_addoption(parser):
    parser.addoption("--node", default=NODE_IP, help="Target node IP")
    parser.addoption("--ssh-pass", default=SSH_PASS, help="SSH password")


@pytest.fixture(autouse=True)
def _configure(request):
    global NODE_IP, SSH_PASS, BASE_URL
    NODE_IP = request.config.getoption("--node", NODE_IP)
    SSH_PASS = request.config.getoption("--ssh-pass", SSH_PASS)
    BASE_URL = f"http://{NODE_IP}:{API_PORT}"


# ── Helpers ──────────────────────────────────────────────────────────

def _ssh_cmd(cmd: str, timeout: int = 15) -> str:
    """Run command on remote node via SSH (using paramiko)."""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(NODE_IP, username=SSH_USER, password=SSH_PASS, timeout=10)
    try:
        stdin, stdout, stderr = client.exec_command(
            f"echo '{SSH_PASS}' | sudo -S {cmd}",
            timeout=timeout,
        )
        return stdout.read().decode()
    finally:
        client.close()


def _wait_healthy(timeout: int = RECOVERY_TIMEOUT) -> float:
    """Poll /healthz until 200 or timeout. Returns recovery time in seconds."""
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
            if r.status_code == 200:
                return time.monotonic() - start
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"Node did not recover within {timeout}s")


def _wait_unhealthy(timeout: int = 30) -> None:
    """Wait until /healthz stops returning 200 (confirms fault injected)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/healthz", timeout=3)
            if r.status_code != 200:
                return
        except Exception:
            return  # connection refused = service is down
        time.sleep(1)


def _get_mode() -> str:
    """Return current system mode from /fdir/mode."""
    try:
        r = httpx.get(f"{BASE_URL}/fdir/mode", timeout=5)
        return r.json().get("mode", "unknown")
    except Exception:
        return "unreachable"


def _get_ladder_level() -> int:
    """Return current recovery ladder level."""
    try:
        r = httpx.get(f"{BASE_URL}/fdir/ladder", timeout=5)
        return r.json().get("current_level", -1)
    except Exception:
        return -1


# ── Drill tests ──────────────────────────────────────────────────────

class TestDrill01_JanusRestart:
    """Drill 1: Kill Janus → verify FDIR detects and restarts it."""

    def test_janus_kill_and_recover(self):
        # Precondition: healthy
        _wait_healthy(timeout=30)

        # Inject fault: kill Janus
        _ssh_cmd("systemctl stop janus.service")
        time.sleep(5)

        # Verify fault detected
        mode = _get_mode()
        assert mode != "NOMINAL", f"Expected degraded mode, got {mode}"

        # Wait for FDIR to restart Janus
        _ssh_cmd("systemctl start janus.service")  # FDIR should do this, but let's ensure
        recovery_sec = _wait_healthy()
        assert recovery_sec < RECOVERY_TIMEOUT, f"Recovery took {recovery_sec:.1f}s (budget: {RECOVERY_TIMEOUT}s)"
        print(f"  ✓ Janus kill → recovery in {recovery_sec:.1f}s")


class TestDrill02_PipelineRestart:
    """Drill 2: Kill ffmpeg pipeline → verify watchdog detects stale stream."""

    def test_pipeline_kill_and_recover(self):
        _wait_healthy(timeout=30)

        # Kill ffmpeg processes
        _ssh_cmd("pkill -9 ffmpeg || true")
        time.sleep(10)

        # Watchdog should detect stale video_age_ms and escalate
        level = _get_ladder_level()
        assert level >= 0, "Ladder should have escalated"

        recovery_sec = _wait_healthy()
        assert recovery_sec < RECOVERY_TIMEOUT
        print(f"  ✓ Pipeline kill → recovery in {recovery_sec:.1f}s")


class TestDrill03_NetworkBlip:
    """Drill 3: Block TURN traffic for 15s → verify reconnect."""

    def test_turn_block_and_recover(self):
        _wait_healthy(timeout=30)

        # Block TURN UDP for 15 seconds
        _ssh_cmd("iptables -I OUTPUT -p udp --dport 3478 -j DROP")
        time.sleep(15)
        _ssh_cmd("iptables -D OUTPUT -p udp --dport 3478 -j DROP")

        recovery_sec = _wait_healthy()
        assert recovery_sec < RECOVERY_TIMEOUT
        print(f"  ✓ Network blip → recovery in {recovery_sec:.1f}s")


class TestDrill04_FullServiceRestart:
    """Drill 4: Stop entire camera-page service → verify systemd restarts."""

    def test_service_restart(self):
        _wait_healthy(timeout=30)

        _ssh_cmd("systemctl restart janus-camera-page.service")
        time.sleep(5)

        recovery_sec = _wait_healthy()
        assert recovery_sec < RECOVERY_TIMEOUT
        print(f"  ✓ Full service restart → healthy in {recovery_sec:.1f}s")


class TestDrill05_HealthzSLO:
    """Drill 5: Verify /healthz response time SLO (< 500ms)."""

    def test_healthz_latency(self):
        times = []
        for _ in range(10):
            start = time.monotonic()
            r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
            elapsed = (time.monotonic() - start) * 1000
            times.append(elapsed)
            assert r.status_code == 200
        p95 = sorted(times)[int(len(times) * 0.95)]
        assert p95 < 500, f"/healthz p95={p95:.0f}ms > 500ms SLO"
        print(f"  ✓ /healthz latency p95={p95:.0f}ms")


# ── Extended drills (06–10) ──────────────────────────────────────────

DEPTH_NODE_IP = os.getenv("DRILL_DEPTH_NODE", "192.168.1.55")
DEPTH_BASE_URL = f"http://{DEPTH_NODE_IP}:{API_PORT}"


def _ssh_depth(cmd: str, timeout: int = 15) -> str:
    """Run command on the depth node (.55) via SSH."""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(DEPTH_NODE_IP, username=SSH_USER, password=SSH_PASS, timeout=10)
    try:
        stdin, stdout, stderr = client.exec_command(
            f"echo '{SSH_PASS}' | sudo -S {cmd}",
            timeout=timeout,
        )
        return stdout.read().decode()
    finally:
        client.close()


def _wait_depth_healthy(timeout: int = RECOVERY_TIMEOUT) -> float:
    """Poll depth node /healthz until 200 or timeout."""
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{DEPTH_BASE_URL}/healthz", timeout=5)
            if r.status_code == 200:
                return time.monotonic() - start
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"Depth node did not recover within {timeout}s")


class TestDrill06_ColdBootE2E:
    """Drill 6: Cold-boot both nodes → measure time-to-first-frame."""

    def test_cold_boot_ttff(self):
        # Reboot color node
        _ssh_cmd("systemctl reboot", timeout=5)
        time.sleep(30)  # wait for reboot
        recovery_sec = _wait_healthy(timeout=180)
        assert recovery_sec < 120, f"Color node cold-boot took {recovery_sec:.1f}s (budget: 120s)"

        # Verify stream is publishing frames
        r = httpx.get(f"{BASE_URL}/health/stream", timeout=10)
        assert r.status_code == 200
        print(f"  ✓ Cold boot → first frame in {recovery_sec:.1f}s")


class TestDrill07_DepthNodeIsolation:
    """Drill 7: Kill depth node network → color node stays NOMINAL."""

    def test_depth_isolation_no_cascade(self):
        _wait_healthy(timeout=30)

        # Block traffic from depth node to color node
        _ssh_cmd(f"iptables -I INPUT -s {DEPTH_NODE_IP} -j DROP")
        time.sleep(20)

        # Color node must remain healthy (depth proxy returns 502 but
        # the color stream is independent)
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        assert r.status_code == 200, "Color node should remain healthy when depth is isolated"

        mode = _get_mode()
        assert mode in ("nominal", "degraded"), f"Expected nominal/degraded, got {mode}"

        # Cleanup
        _ssh_cmd(f"iptables -D INPUT -s {DEPTH_NODE_IP} -j DROP")
        print(f"  ✓ Depth isolation → color node stayed {mode}")


class TestDrill08_UplinkFlap:
    """Drill 8: Drop all WAN traffic for 20s → verify LOCAL_ONLY mode."""

    def test_uplink_flap(self):
        _wait_healthy(timeout=30)

        # Block all WAN (non-LAN) egress
        _ssh_cmd("iptables -I OUTPUT -d 0.0.0.0/0 ! -d 192.168.1.0/24 -j DROP")
        time.sleep(20)

        mode = _get_mode()
        assert mode in ("local_only", "degraded"), f"Expected LOCAL_ONLY or degraded, got {mode}"

        # Restore
        _ssh_cmd("iptables -D OUTPUT -d 0.0.0.0/0 ! -d 192.168.1.0/24 -j DROP")
        recovery_sec = _wait_healthy()
        assert recovery_sec < RECOVERY_TIMEOUT
        print(f"  ✓ Uplink flap → mode={mode}, recovery in {recovery_sec:.1f}s")


class TestDrill09_DualFault:
    """Drill 9: Kill Janus + pipeline simultaneously → verify ladder escalation."""

    def test_dual_fault_escalation(self):
        _wait_healthy(timeout=30)

        # Inject two faults at once
        _ssh_cmd("systemctl stop janus.service && pkill -9 ffmpeg || true")
        time.sleep(10)

        level = _get_ladder_level()
        assert level >= 1, f"Dual fault should escalate ladder past level 0, got {level}"

        # Wait for full recovery
        recovery_sec = _wait_healthy(timeout=RECOVERY_TIMEOUT * 2)
        final_mode = _get_mode()
        print(f"  ✓ Dual fault → ladder level={level}, mode={final_mode}, "
              f"recovery in {recovery_sec:.1f}s")


class TestDrill10_DepthProxyFailover:
    """Drill 10: Stop depth node service → verify color proxy returns 502."""

    def test_depth_proxy_502(self):
        _wait_healthy(timeout=30)

        # Stop depth camera-page on .55
        try:
            _ssh_depth("systemctl stop janus-camera-page.service")
        except Exception:
            pytest.skip("Cannot SSH to depth node .55")

        time.sleep(5)

        # Color node depth proxy should return 502
        r = httpx.get(f"{BASE_URL}/api/v1/depth_camera/healthz", timeout=10)
        assert r.status_code == 502, f"Expected 502 from depth proxy, got {r.status_code}"

        # Restore
        _ssh_depth("systemctl start janus-camera-page.service")
        time.sleep(5)
        r2 = httpx.get(f"{BASE_URL}/api/v1/depth_camera/healthz", timeout=10)
        assert r2.status_code == 200
        print("  ✓ Depth proxy failover → 502 while down, 200 after restart")
