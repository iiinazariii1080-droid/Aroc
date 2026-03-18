"""Unit tests for app.core.service_metrics — thread-safe metrics recording."""

import threading

import pytest

from app.core.service_metrics import (
    get_service_error_metrics,
    record_auth_result,
    record_proxy_result,
    reset_service_error_metrics,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_service_error_metrics()
    yield
    reset_service_error_metrics()


class TestRecordProxyResult:
    def test_records_success(self):
        record_proxy_result("xarm", 200)
        m = get_service_error_metrics()
        assert m["proxy_services"]["xarm"]["total"] == 1
        assert m["proxy_services"]["xarm"]["errors"] == 0
        assert m["proxy_services"]["xarm"]["error_rate"] == 0.0

    def test_records_server_error(self):
        record_proxy_result("igus", 502, reason="connect_failed")
        m = get_service_error_metrics()
        bucket = m["proxy_services"]["igus"]
        assert bucket["total"] == 1
        assert bucket["errors"] == 1
        assert bucket["error_rate"] == 1.0
        assert bucket["error_reasons"]["connect_failed"] == 1
        assert bucket["last_error_reason"] == "connect_failed"
        assert bucket["last_error_at"] is not None

    def test_error_rate_calculation(self):
        for _ in range(3):
            record_proxy_result("robot", 200)
        record_proxy_result("robot", 500)
        m = get_service_error_metrics()
        assert m["proxy_services"]["robot"]["error_rate"] == pytest.approx(0.25)

    def test_status_codes_tracked(self):
        record_proxy_result("xarm", 200)
        record_proxy_result("xarm", 201)
        record_proxy_result("xarm", 502)
        m = get_service_error_metrics()
        statuses = m["proxy_services"]["xarm"]["statuses"]
        assert statuses["200"] == 1
        assert statuses["201"] == 1
        assert statuses["502"] == 1

    def test_multiple_services_isolated(self):
        record_proxy_result("xarm", 200)
        record_proxy_result("igus", 500)
        m = get_service_error_metrics()
        assert m["proxy_services"]["xarm"]["errors"] == 0
        assert m["proxy_services"]["igus"]["errors"] == 1

    def test_4xx_not_counted_as_error(self):
        record_proxy_result("xarm", 404)
        m = get_service_error_metrics()
        assert m["proxy_services"]["xarm"]["errors"] == 0

    def test_default_error_reason(self):
        record_proxy_result("xarm", 500)
        m = get_service_error_metrics()
        assert m["proxy_services"]["xarm"]["error_reasons"]["status_500"] == 1


class TestRecordAuthResult:
    def test_records_success(self):
        record_auth_result(True)
        m = get_service_error_metrics()
        assert m["auth"]["attempts"] == 1
        assert m["auth"]["failures"] == 0

    def test_records_failure(self):
        record_auth_result(False, "connect_timeout")
        m = get_service_error_metrics()
        assert m["auth"]["attempts"] == 1
        assert m["auth"]["failures"] == 1
        assert m["auth"]["failure_reasons"]["connect_timeout"] == 1

    def test_error_rate(self):
        record_auth_result(True)
        record_auth_result(False, "timeout")
        m = get_service_error_metrics()
        assert m["auth"]["error_rate"] == pytest.approx(0.5)


class TestThreadSafety:
    def test_concurrent_writes(self):
        """Verify metrics don't corrupt under concurrent access."""
        errors = []

        def writer(service, count):
            try:
                for i in range(count):
                    record_proxy_result(service, 200 if i % 2 == 0 else 500)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("xarm", 100)),
            threading.Thread(target=writer, args=("igus", 100)),
            threading.Thread(target=writer, args=("xarm", 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        m = get_service_error_metrics()
        assert m["proxy_services"]["xarm"]["total"] == 200
        assert m["proxy_services"]["igus"]["total"] == 100


class TestReset:
    def test_reset_clears_all(self):
        record_proxy_result("xarm", 200)
        record_auth_result(False, "err")
        reset_service_error_metrics()
        m = get_service_error_metrics()
        assert m["proxy_services"] == {}
        assert m["auth"]["attempts"] == 0
        assert m["auth"]["failures"] == 0
