"""Tests for config validation (TEST-02)."""

import pytest

from app.config import Settings
from app.state import _validate_settings


class TestConfigValidation:
    """Ensure _validate_settings catches invalid config combinations."""

    def test_valid_defaults(self):
        s = Settings()
        _validate_settings(s)  # should not raise

    def test_empty_host_rejected(self):
        s = Settings(dryve_host="")
        with pytest.raises(ValueError, match="DRYVE_HOST"):
            _validate_settings(s)

    def test_connect_timeout_zero_rejected(self):
        s = Settings(dryve_connect_timeout_s=0)
        with pytest.raises(ValueError, match="DRYVE_CONNECT_TIMEOUT_S"):
            _validate_settings(s)

    def test_connect_timeout_too_large_rejected(self):
        s = Settings(dryve_connect_timeout_s=60)
        with pytest.raises(ValueError, match="DRYVE_CONNECT_TIMEOUT_S"):
            _validate_settings(s)

    def test_request_timeout_zero_rejected(self):
        s = Settings(dryve_request_timeout_s=0)
        with pytest.raises(ValueError, match="DRYVE_REQUEST_TIMEOUT_S"):
            _validate_settings(s)

    def test_request_timeout_too_large_rejected(self):
        s = Settings(dryve_request_timeout_s=30)
        with pytest.raises(ValueError, match="DRYVE_REQUEST_TIMEOUT_S"):
            _validate_settings(s)

    def test_min_ge_max_position_rejected(self):
        s = Settings(dryve_min_position_limit=120000, dryve_max_position_limit=120000)
        with pytest.raises(ValueError, match="MIN_POSITION_LIMIT"):
            _validate_settings(s)

    def test_retry_base_gt_max_rejected(self):
        s = Settings(dryve_retry_base_delay_s=10.0, dryve_retry_max_delay_s=5.0)
        with pytest.raises(ValueError, match="RETRY_BASE_DELAY"):
            _validate_settings(s)

    def test_keepalive_zero_rejected(self):
        s = Settings(dryve_keepalive_interval_s=0)
        with pytest.raises(ValueError, match="KEEPALIVE_INTERVAL"):
            _validate_settings(s)

    def test_negative_jog_ttl_rejected(self):
        s = Settings(dryve_jog_ttl_ms=-1)
        with pytest.raises(ValueError, match="JOG_TTL"):
            _validate_settings(s)

    def test_readiness_threshold_out_of_range_rejected(self):
        s = Settings(dryve_health_readiness_threshold=101)
        with pytest.raises(ValueError, match="READINESS_THRESHOLD"):
            _validate_settings(s)

    def test_readiness_threshold_negative_rejected(self):
        s = Settings(dryve_health_readiness_threshold=-1)
        with pytest.raises(ValueError, match="READINESS_THRESHOLD"):
            _validate_settings(s)

    def test_retry_base_eq_max_accepted(self):
        s = Settings(dryve_retry_base_delay_s=5.0, dryve_retry_max_delay_s=5.0)
        _validate_settings(s)  # should not raise

    def test_readiness_threshold_boundary_accepted(self):
        for t in (0, 50, 100):
            s = Settings(dryve_health_readiness_threshold=t)
            _validate_settings(s)  # should not raise
