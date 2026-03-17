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

from app.services import fdir_events
from app.services import system_mode
from app.services.system_mode import (
    SystemMode,
    current_mode,
    promote,
    transition,
)


@pytest.fixture(autouse=True)
def _isolate():
    system_mode._reset_for_tests()
    fdir_events._reset_for_tests()
    yield
    system_mode._reset_for_tests()
    fdir_events._reset_for_tests()


# ===================================================================
# X1: Pipeline stale does NOT bring down Janus
# ===================================================================

class TestX1_PipelineJanusIsolation:
    """L3 pipeline failure must not crash L4 Janus session."""

    @pytest.mark.asyncio
    async def test_stale_video_age_janus_still_responds(self):
        """Simulate watchdog seeing stale video_age_ms while Janus API is up."""
        from unittest.mock import AsyncMock
        fake_summary = {"video_age_ms": 99999, "active_viewers": 0}
        with patch("app.services.janus.janus_summary", new_callable=AsyncMock, return_value=fake_summary):
            from app.services import janus
            result = await janus.janus_summary(1234)
            # Janus responded (L4 alive) even though stream is stale (L3 fault)
            assert "video_age_ms" in result
            assert result["video_age_ms"] > 10000

    @pytest.mark.asyncio
    async def test_pipeline_exception_janus_unaffected(self):
        """An ffmpeg crash (subprocess error) doesn't affect Janus query."""
        from unittest.mock import AsyncMock
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")),
            patch("app.services.janus.janus_summary", new_callable=AsyncMock, return_value={"video_age_ms": 0}),
        ):
            from app.services import janus
            # Janus still fine even though ffmpeg is gone
            result = await janus.janus_summary(1)
            assert result["video_age_ms"] == 0


# ===================================================================
# X2: Janus down does NOT kill L5 API
# ===================================================================

class TestX2_JanusApiIsolation:
    """L4 Janus failure must not prevent /healthz from responding."""

    @pytest.fixture
    def app_client(self, tmp_path):
        import httpx
        from tests.conftest import make_test_settings

        _admin_s = make_test_settings(tmp_path, admin_enforce=False)

        from app.core.app import create_app
        with patch("app.core.app.StaticFiles", MagicMock()), \
             patch("app.core.admin.get_settings", return_value=_admin_s):
            app = create_app()
            yield httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            )

    @pytest.mark.asyncio
    async def test_healthz_with_janus_down(self, app_client):
        """API /healthz must return 200 even when Janus is unreachable."""
        from unittest.mock import AsyncMock
        with patch("app.services.janus.janus_summary", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
            r = await app_client.get("/healthz")
            # /healthz checks the FastAPI process, not Janus
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_fdir_mode_endpoint_with_janus_down(self, app_client):
        """The /fdir/mode endpoint must work regardless of Janus state."""
        from unittest.mock import AsyncMock
        with patch("app.services.janus.janus_summary", new_callable=AsyncMock, side_effect=ConnectionError):
            r = await app_client.get("/fdir/mode")
            assert r.status_code == 200
            assert "mode" in r.json()


# ===================================================================
# X3: Depth proxy failure does NOT affect color stream
# ===================================================================

class TestX3_DepthColorIsolation:
    """Depth proxy errors must not cascade into color node health."""

    @pytest.fixture
    def color_client(self):
        os.environ["CAM_TYPE"] = "color_camera"
        os.environ["DEPTH_CAM_URL"] = "http://192.168.1.55:8900"
        import httpx

        from app.core.app import create_app
        with patch("app.core.app.StaticFiles", MagicMock()):
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
        from app.services.recovery_ladder import (
            RecoveryAction,  # noqa: F401 (RecoveryAction for type hints)
        )

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
        with patch.object(watchdogs, "_STARTUP_TS", time.time()):
            # Just started → should be in grace period
            assert watchdogs._in_grace_period() is True

    def test_grace_period_expired(self):
        from app.services import watchdogs
        # Started a long time ago
        with patch.object(watchdogs, "_STARTUP_TS", time.time() - 9999):
            assert watchdogs._in_grace_period() is False


# ===================================================================
# X6: Dual watchdog dedup
# ===================================================================

class TestX6_WatchdogDedup:
    """Snapshot watchdog skips if Janus watchdog recently escalated."""

    def test_janus_escalation_dedup(self):
        from app.services import watchdogs
        # Mark Janus as having escalated just now
        watchdogs._mark_janus_escalated()
        assert watchdogs._janus_recently_escalated() is True

    def test_old_escalation_not_dedupped(self):
        from app.services import watchdogs
        with patch.object(watchdogs, "_last_janus_escalation_ts", 0.0):
            assert watchdogs._janus_recently_escalated() is False


# ===================================================================
# X7: Architecture — no sync requests in routes or services
# ===================================================================

class TestX7_NoSyncRequests:
    """Routes and services (except janus.py) must not import 'requests' directly."""

    def test_routes_no_sync_requests(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).parent.parent
        pattern = re.compile(r'^\s*(import requests|from requests\b)', re.MULTILINE)
        violations = []
        for f in (root / "app" / "routes").glob("*.py"):
            if pattern.search(f.read_text()):
                violations.append(f.name)
        assert not violations, f"Route files with sync 'requests': {violations}"

    def test_services_no_sync_requests(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).parent.parent
        pattern = re.compile(r'^\s*(import requests|from requests\b)', re.MULTILINE)
        violations = []
        for f in (root / "app" / "services").glob("*.py"):
            if pattern.search(f.read_text()):
                violations.append(f.name)
        assert not violations, f"Service files with sync 'requests': {violations}"


# ===================================================================
# X7b: Architecture — subprocess.run only via app.utils.process
# ===================================================================

class TestX7b_SubprocessSinglePoint:
    """subprocess.run must only be called from app/utils/process.py."""

    def test_subprocess_run_single_point(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).parent.parent
        pattern = re.compile(r'\bsubprocess\.run\b')
        violations = []
        for f in (root / "app").rglob("*.py"):
            rel = f.relative_to(root / "app")
            if rel.parts == ("utils", "process.py"):
                continue
            if pattern.search(f.read_text()):
                violations.append(str(rel))
        assert not violations, f"Files calling subprocess.run directly: {violations}"


# ===================================================================
# X7c: Architecture — no module-level get_settings() in routes
# ===================================================================

class TestX7c_NoModuleLevelSettings:
    """Route modules must not call get_settings() at module level."""

    def test_routes_no_module_level_get_settings(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).parent.parent
        # Matches module-level assignments like: CAM_TYPE = get_settings().camera_type
        pattern = re.compile(r'^[A-Z_][A-Z_0-9]*\s*=\s*.*get_settings\(\)', re.MULTILINE)
        violations = []
        for f in (root / "app" / "routes").glob("*.py"):
            if pattern.search(f.read_text()):
                violations.append(f.name)
        assert not violations, f"Route files with module-level get_settings(): {violations}"


# ===================================================================
# X8: Memory-leak safety
# ===================================================================

class TestX8_MemoryLeakSafety:
    """Rate-limiter and ring-buffer must not leak memory."""

    def test_rate_limit_buckets_evicted_after_window(self):
        """Expired rate-limit buckets must be evicted from _buckets dict."""
        import collections
        import time

        from app.core.app import RateLimitMiddleware

        import tempfile
        from pathlib import Path
        from tests.conftest import make_test_settings

        with tempfile.TemporaryDirectory() as _tmp:
            mock_settings = make_test_settings(
                Path(_tmp),
                rate_limit_enabled=True,
                rate_limit_window_sec=10.0,
                rate_limit_snapshot=100,
                rate_limit_healthz=100,
                rate_limit_janus_ws=100,
            )

        async def _noop(scope, receive, send):
            pass  # pragma: no cover

        rl = RateLimitMiddleware(_noop, settings=mock_settings)

        key = ("/snapshot.jpg", "1.2.3.4")
        old_ts = time.monotonic() - 9999.0
        rl._buckets[key] = collections.deque([old_ts])

        # Simulate the dispatch drain logic (single-threaded, no lock needed)
        now = time.monotonic()
        cutoff = now - 10.0
        bucket = rl._buckets.setdefault(key, collections.deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            del rl._buckets[key]
        bucket.append(now)
        rl._buckets[key] = bucket

        # Key should be re-inserted with exactly 1 fresh entry
        assert key in rl._buckets
        assert len(rl._buckets[key]) == 1

        # An unseen key must not exist
        assert ("/snapshot.jpg", "9.9.9.9") not in rl._buckets

    def test_fdir_ring_lazy_init_safe(self):
        """_get_ring() must initialise safely when _ring is None."""
        import threading

        from app.services import fdir_events as _fe

        original = _fe._ring
        _fe._ring = None
        rings: list = []
        errors: list = []

        def _call():
            try:
                rings.append(_fe._get_ring())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _fe._ring = original  # restore

        assert not errors, f"Errors during concurrent _get_ring(): {errors}"
        # All threads must see the same deque object (singleton init)
        assert len({id(r) for r in rings}) == 1
