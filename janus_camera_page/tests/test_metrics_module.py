"""Tests to verify the metrics module refactor — all metrics importable from app.metrics."""
from __future__ import annotations

import pytest


class TestMetricsImport:
    """Verify all metrics are accessible from app.metrics (not routes)."""

    def test_gauges_importable(self):
        from app.metrics import (
            system_mode,
            recovery_ladder_level,
            stream_active,
            janus_reachable,
            video_age_ms,
            cpu_temp_celsius,
            client_packet_loss_ratio,
            client_frames_decoded_total,
            client_last_report_age_seconds,
        )
        # Verify they are actual prometheus gauges
        assert hasattr(system_mode, "set")
        assert hasattr(recovery_ladder_level, "set")
        assert hasattr(stream_active, "set")
        assert hasattr(video_age_ms, "set")

    def test_counters_importable(self):
        from app.metrics import (
            watchdog_checks_total,
            watchdog_healthy_total,
            watchdog_escalations_total,
            fdir_events_total,
            mode_transitions_total,
            ice_connects_total,
        )
        assert hasattr(watchdog_checks_total, "inc")
        assert hasattr(ice_connects_total, "inc")

    def test_histograms_importable(self):
        from app.metrics import (
            ice_connect_duration_seconds,
            ttff_seconds,
        )
        assert hasattr(ice_connect_duration_seconds, "observe")
        assert hasattr(ttff_seconds, "observe")

    def test_re_exports_from_routes_metrics(self):
        """Backwards compatibility: routes.metrics re-exports everything."""
        from app.routes.metrics import system_mode as rm_sm
        from app.metrics import system_mode as m_sm
        # Should be the exact same object (not a copy)
        assert rm_sm is m_sm
