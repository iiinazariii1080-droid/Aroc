"""
Integration tests: spin up simulator (main-2.py) + app (main.py),
then exercise every /api/v1 endpoint via HTTP.

Run:  pytest drivers/tests/integration/test_simulator_api.py -v --tb=short -m simulator
Skip: pytest -m "not simulator"   (default — these are slow)
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Generator

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SIMULATOR_SCRIPT = os.path.join(REPO_ROOT, "main-2.py")
APP_SCRIPT = os.path.join(REPO_ROOT, "main.py")

SIM_MODBUS_PORT = 15502  # non-privileged port for CI
SIM_HTTP_PORT = 18001
APP_PORT = 18101

BASE_URL = f"http://127.0.0.1:{APP_PORT}"

STARTUP_TIMEOUT = 20  # seconds
POLL_INTERVAL = 0.3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _wait_for_port(port: int, timeout: float = STARTUP_TIMEOUT) -> None:
    """Block until *port* accepts TCP connections."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Port {port} not ready after {timeout}s")


def _wait_for_http(url: str, timeout: float = STARTUP_TIMEOUT) -> None:
    """Block until *url* returns HTTP 200."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{url} not reachable after {timeout}s")


def _api(method: str, path: str, body: dict | None = None,
         timeout: int = 60) -> tuple[int, dict]:
    """Low-level HTTP helper — returns (status_code, json_body)."""
    url = BASE_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _sim_state() -> dict:
    """Read the latest SSE snapshot from the simulator's /events stream."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", SIM_HTTP_PORT, timeout=3)
    conn.request("GET", "/events")
    resp = conn.getresponse()
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 2.0:
        chunk = resp.read(4096)
        if chunk:
            buf += chunk
            if b"\n\n" in buf:
                break
    conn.close()
    for line in buf.decode(errors="replace").split("\n"):
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return {}


# ---------------------------------------------------------------------------
# Fixtures — session-scoped (start once per test session)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def simulator_proc() -> Generator[subprocess.Popen, None, None]:
    """Start the Modbus TCP simulator (main-2.py) as a subprocess."""
    env = {
        **os.environ,
        "MODBUS_LISTEN_PORT": str(SIM_MODBUS_PORT),
        "HTTP_PORT": str(SIM_HTTP_PORT),
    }
    proc = subprocess.Popen(
        [sys.executable, SIMULATOR_SCRIPT],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        _wait_for_port(SIM_MODBUS_PORT)
        _wait_for_port(SIM_HTTP_PORT)
        yield proc
    finally:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


@pytest.fixture(scope="session")
def app_proc(simulator_proc) -> Generator[subprocess.Popen, None, None]:  # noqa: ARG001
    """Start the FastAPI app (main.py → uvicorn) pointing at the simulator."""
    env = {
        **os.environ,
        "DRYVE_HOST": "127.0.0.1",
        "DRYVE_PORT": str(SIM_MODBUS_PORT),
        "DRYVE_UNIT_ID": "0",
        "DRYVE_ALLOW_TID_MISMATCH": "1",
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "main:app",
            "--host", "127.0.0.1",
            "--port", str(APP_PORT),
            "--log-level", "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        _wait_for_http(f"http://127.0.0.1:{APP_PORT}/health")
        yield proc
    finally:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


@pytest.fixture(scope="session")
def api(app_proc) -> Any:  # noqa: ARG001
    """Return the _api helper once the app is confirmed alive."""
    return _api


# ═══════════════════════════════════════════════════════════════════════════
# Tests — ordered logically (health → reference → motion → jog → stop)
# ═══════════════════════════════════════════════════════════════════════════

pytestmark = pytest.mark.simulator


class TestHealthEndpoints:
    """GET /health, /ready, /info — basic liveness."""

    def test_health(self, api):
        code, body = api("GET", "/health")
        assert code == 200
        assert body.get("status") == "ok"

    def test_ready(self, api):
        code, body = api("GET", "/ready")
        assert code == 200
        assert body.get("status") == "ready"

    def test_info(self, api):
        code, body = api("GET", "/info")
        assert code == 200
        assert "server_version" in body


class TestStatusEndpoints:
    """Drive status & telemetry."""

    def test_v1_status(self, api):
        code, body = api("GET", "/api/v1/drive/status")
        assert code == 200
        assert body.get("ok") is True
        data = body["data"]
        assert "cia402_state" in data
        assert "statusword" in data
        assert "position" in data

    def test_v1_telemetry(self, api):
        code, body = api("GET", "/api/v1/drive/telemetry")
        assert code == 200
        assert body.get("ok") is True

    def test_legacy_status(self, api):
        code, body = api("GET", "/status")
        assert code == 200

    def test_legacy_position(self, api):
        code, body = api("GET", "/position")
        assert code == 200

    def test_legacy_is_motion(self, api):
        code, body = api("GET", "/is_motion")
        assert code == 200


class TestFaultReset:
    """Fault reset — should succeed even if no fault active."""

    def test_v1_fault_reset(self, api):
        code, body = api("POST", "/api/v1/drive/fault_reset",
                         {"timeout_ms": 15000})
        assert code == 200
        assert body.get("ok") is True

    def test_legacy_fault_reset(self, api):
        code, body = api("POST", "/fault_reset")
        assert code == 200


class TestReference:
    """Homing / reference run."""

    def test_v1_reference(self, api):
        code, body = api("POST", "/api/v1/drive/reference",
                         {"timeout_ms": 15000})
        assert code == 200
        assert body.get("ok") is True

    def test_position_after_homing(self, api):
        """Position should be near 0 after successful homing."""
        code, body = api("GET", "/api/v1/drive/status")
        assert code == 200
        pos = body["data"].get("position")
        assert pos is not None
        assert abs(pos) < 100, f"Expected near-zero position after homing, got {pos}"


class TestMoveToPosition:
    """Profile-position moves."""

    _PROFILE = {"velocity": 5000, "acceleration": 3000, "deceleration": 3000}

    def test_move_to_10000(self, api):
        code, body = api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 10000,
            "profile": self._PROFILE,
            "timeout_ms": 15000,
        })
        assert code == 200
        assert body.get("ok") is True

    def test_position_at_10000(self, api):
        time.sleep(0.5)  # let telemetry cache refresh
        code, body = api("GET", "/api/v1/drive/status")
        pos = body["data"]["position"]
        assert abs(pos - 10000) < 500, f"Expected ~10000, got {pos}"

    def test_move_to_50000(self, api):
        code, body = api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 50000,
            "profile": {"velocity": 10000, "acceleration": 5000, "deceleration": 5000},
            "timeout_ms": 15000,
        })
        assert code == 200
        assert body.get("ok") is True

    def test_position_at_50000(self, api):
        time.sleep(0.5)
        code, body = api("GET", "/api/v1/drive/status")
        pos = body["data"]["position"]
        assert abs(pos - 50000) < 500, f"Expected ~50000, got {pos}"

    def test_move_back_to_zero(self, api):
        code, body = api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 0,
            "profile": {"velocity": 20000, "acceleration": 10000, "deceleration": 10000},
            "timeout_ms": 30000,
        })
        assert code == 200
        assert body.get("ok") is True
        # Verify position settled near zero
        time.sleep(1.5)
        _, st = api("GET", "/api/v1/drive/status")
        pos = st["data"]["position"]
        assert abs(pos) < 2000, f"Expected ~0, got {pos}"


class TestRelativeMove:
    """Relative profile-position move."""

    def test_relative_plus_5000(self, api):
        # Move to a known baseline first
        api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 10000,
            "profile": {"velocity": 10000, "acceleration": 5000, "deceleration": 5000},
            "timeout_ms": 15000,
        })
        time.sleep(1.0)

        # Read current position
        _, pre = api("GET", "/api/v1/drive/status")
        pre_pos = pre["data"]["position"]

        code, body = api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 5000,
            "relative": True,
            "profile": {"velocity": 5000, "acceleration": 3000, "deceleration": 3000},
            "timeout_ms": 15000,
        })
        assert code == 200
        assert body.get("ok") is True

        # Check position changed by ~5000 (wide tolerance for simulator timing)
        time.sleep(1.0)
        _, post = api("GET", "/api/v1/drive/status")
        post_pos = post["data"]["position"]
        delta = post_pos - pre_pos
        assert abs(delta - 5000) < 2000, f"Expected delta ~5000, got {delta}"


class TestJog:
    """Jog start / stop."""

    def test_jog_positive(self, api):
        code, body = api("POST", "/api/v1/drive/jog_start", {
            "direction": "positive",
            "speed": 3000,
            "ttl_ms": 2000,
        })
        assert code == 200
        assert body.get("ok") is True

    def test_jog_stop_after_positive(self, api):
        time.sleep(0.5)
        code, body = api("POST", "/api/v1/drive/jog_stop")
        assert code == 200
        assert body.get("ok") is True

    def test_jog_negative(self, api):
        # First move to a safe mid-range position so negative jog is within limits
        api("POST", "/api/v1/drive/move_to_position", {
            "target_position": 60000,
            "profile": {"velocity": 10000, "acceleration": 5000, "deceleration": 5000},
            "timeout_ms": 15000,
        })
        code, body = api("POST", "/api/v1/drive/jog_start", {
            "direction": "negative",
            "speed": 3000,
            "ttl_ms": 2000,
        })
        assert code == 200
        assert body.get("ok") is True

    def test_jog_stop_after_negative(self, api):
        time.sleep(0.5)
        code, body = api("POST", "/api/v1/drive/jog_stop")
        assert code == 200
        assert body.get("ok") is True


class TestStop:
    """Quick-stop command."""

    def test_quick_stop(self, api):
        code, body = api("POST", "/api/v1/drive/stop", {
            "mode": "quick_stop",
            "timeout_ms": 5000,
        })
        assert code == 200
        assert body.get("ok") is True


class TestStopDuringMove:
    """CRITICAL: stop must interrupt a running move_to_position immediately."""

    def test_stop_interrupts_long_move(self, api):
        """Start a slow long-distance move, then stop mid-flight.

        The move endpoint should return within a few seconds (aborted),
        NOT block until the 30s timeout.
        """
        import concurrent.futures, threading

        # 1. Start a SLOW move in background (this will block 30s without abort)
        def do_move():
            return _api("POST", "/api/v1/drive/move_to_position", {
                "target_position": 100000,  # far away: position is clamped to 120000
                "profile": {"velocity": 500, "acceleration": 200, "deceleration": 200},
                "timeout_ms": 30000,
            }, timeout=35)

        with concurrent.futures.ThreadPoolExecutor() as pool:
            move_future = pool.submit(do_move)

            # 2. Wait a moment for the move to start
            time.sleep(2.0)

            # 3. Send STOP
            stop_code, stop_body = api("POST", "/api/v1/drive/stop", {
                "mode": "quick_stop",
                "timeout_ms": 5000,
            })
            assert stop_code == 200, f"stop returned {stop_code}: {stop_body}"

            # 4. The move should return quickly (not after 30s)
            move_code, move_body = move_future.result(timeout=10.0)
            assert move_code == 200, f"move returned {move_code}: {move_body}"
            assert move_body.get("ok") is True
            # Should contain aborted flag
            data = move_body.get("data", {})
            assert data.get("aborted") is True, (
                f"Expected aborted=True in response, got {data}"
            )


class TestSimulatorState:
    """Verify simulator SSE stream is reachable and sane."""

    def test_sse_snapshot(self):
        state = _sim_state()
        assert "position" in state
        assert "homed" in state
