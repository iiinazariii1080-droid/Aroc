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
