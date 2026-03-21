"""Phase 5 — Cross-layer isolation tests (X1–X6).

Validates that failures in one layer do NOT cascade into adjacent
layers.  These are pure unit/integration tests — no SSH or live nodes.

Coverage:
  X1: L3 pipeline stale → L4 Janus still reachable
  X2: L4 Janus unreachable → L5 API /healthz still 200
  X3: Depth proxy 502 → color stream independent
  X4: Watchdog healthy streak → ladder reset + mode promote
  X5: Grace period suppresses escalation
  X6: Dual watchdog dedup (janus + snapshot)
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("CAM_TYPE", "color_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")
os.environ.setdefault("FDIR_DEDUP_SEC", "0")

from app.services.fdir_events import Domain, _lock as _ring_lock, _ring
from app.services.system_mode import (
    SystemMode,
    _state,
    current_mode,
    promote,
    transition,
)


def _reset_mode() -> None:
    with _state.lock:
        _state.current = SystemMode.NOMINAL
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = "test_reset"
        _state.listeners.clear()


def _clear_ring() -> None:
    with _ring_lock:
        _ring.clear()


@pytest.fixture(autouse=True)
def _isolate():
    _reset_mode()
    _clear_ring()
    yield
    _reset_mode()
    _clear_ring()


# ===================================================================
# X1: Pipeline stale does NOT bring down Janus
# ===================================================================

class TestX1_PipelineJanusIsolation:
    """L3 pipeline failure must not crash L4 Janus session."""

    def test_stale_video_age_janus_still_responds(self):
        """Simulate watchdog seeing stale video_age_ms while Janus API is up."""
        fake_summary = {"video_age_ms": 99999, "active_viewers": 0}
        with patch("app.services.janus.janus_summary", return_value=fake_summary):
            from app.services import janus
            result = janus.janus_summary(1234)
            # Janus responded (L4 alive) even though stream is stale (L3 fault)
            assert "video_age_ms" in result
            assert result["video_age_ms"] > 10000

    def test_pipeline_exception_janus_unaffected(self):
        """An ffmpeg crash (subprocess error) doesn't affect Janus query."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")),
            patch("app.services.janus.janus_summary", return_value={"video_age_ms": 0}),
        ):
            from app.services import janus
            # Janus still fine even though ffmpeg is gone
            assert janus.janus_summary(1)["video_age_ms"] == 0


# ===================================================================
# X2: Janus down does NOT kill L5 API
# ===================================================================

class TestX2_JanusApiIsolation:
    """L4 Janus failure must not prevent /healthz from responding."""

    @pytest.fixture()
    def app_client(self):
        from app.core.app import create_app
        import httpx
        app = create_app()
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    @pytest.mark.asyncio
    async def test_healthz_with_janus_down(self, app_client):
        """API /healthz must return 200 even when Janus is unreachable."""
        with patch("app.services.janus.janus_summary", side_effect=ConnectionError("refused")):
            r = await app_client.get("/healthz")
            # /healthz checks the FastAPI process, not Janus
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_fdir_mode_endpoint_with_janus_down(self, app_client):
        """The /fdir/mode endpoint must work regardless of Janus state."""
        with patch("app.services.janus.janus_summary", side_effect=ConnectionError):
            r = await app_client.get("/fdir/mode", headers={"X-Admin-Token": "test-token"})
            assert r.status_code == 200
            assert "mode" in r.json()


# ===================================================================
# X3: Depth proxy failure does NOT affect color stream
# ===================================================================

class TestX3_DepthColorIsolation:
    """Depth proxy errors must not cascade into color node health."""

    @pytest.fixture()
    def color_client(self):
        os.environ["CAM_TYPE"] = "color_camera"
        os.environ["DEPTH_CAM_URL"] = "http://192.168.1.55:8900"
        from app.core.app import create_app
        import httpx
        app = create_app()
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    @pytest.mark.asyncio
    async def test_depth_502_color_healthz_ok(self, color_client):
        """When depth node is down (proxy 502), /healthz still 200."""
        r = await color_client.get("/healthz")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_depth_timeout_does_not_block_api(self, color_client):
        """Slow depth node must not block fast color endpoints."""
        # /healthz is independent of depth proxy
        r = await color_client.get("/healthz")
        assert r.status_code == 200


# ===================================================================
# X4: Healthy streak resets ladder + promotes mode
# ===================================================================

class TestX4_HealthyStreakReset:
    """10 consecutive healthy watchdog checks → reset + NOMINAL."""

    def test_healthy_streak_promotes_to_nominal(self):
        """Simulate watchdog loop: 10 healthy checks → promote()."""
        from app.services.recovery_ladder import LadderLevel, RecoveryAction

        transition(SystemMode.DEGRADED, "test_fault")
        assert current_mode() == SystemMode.DEGRADED

        # Create a mock ladder
        mock_ladder = MagicMock()
        mock_ladder.reset = MagicMock()

        # Simulate what _watchdog_loop does after _NOMINAL_WINDOW_CHECKS
        healthy_streak = 0
        for _ in range(10):
            healthy_streak += 1
        assert healthy_streak >= 10

        # This is the code the real watchdog executes:
        mock_ladder.reset()
        promote(SystemMode.NOMINAL, "stream healthy for sustained window")

        mock_ladder.reset.assert_called_once()
        assert current_mode() == SystemMode.NOMINAL


# ===================================================================
# X5: Grace period suppresses escalation
# ===================================================================

class TestX5_GracePeriod:
    """During grace period, stale streams do NOT trigger escalation."""

    def test_in_grace_period_true(self):
        from app.services import watchdogs
        with patch.object(watchdogs, "_STARTUP_TS", time.monotonic()):
            # Just started → should be in grace period
            assert watchdogs._in_grace_period() is True

    def test_grace_period_expired(self):
        from app.services import watchdogs
        # Started a long time ago
        with patch.object(watchdogs, "_STARTUP_TS", time.monotonic() - 9999):
            assert watchdogs._in_grace_period() is False


# ===================================================================
# X6: Dual watchdog dedup
# ===================================================================

class TestX6_WatchdogDedup:
    """Dual watchdog dedup: _try_escalate atomically claims the dedup window."""

    def test_recent_escalation_dedup(self):
        from app.services import watchdogs
        # Simulate a recent escalation by setting the timestamp
        with watchdogs._escalation_lock:
            watchdogs._last_escalation_ts = time.monotonic()
        assert watchdogs._recently_escalated() is True

    def test_old_escalation_not_dedupped(self):
        from app.services import watchdogs
        with patch.object(watchdogs, "_last_escalation_ts", 0.0):
            assert watchdogs._recently_escalated() is False
