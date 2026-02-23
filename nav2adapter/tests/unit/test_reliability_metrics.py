from services.reliability_metrics import ReliabilityMetrics


def test_reliability_metrics_rates_and_duration_snapshot() -> None:
    metrics = ReliabilityMetrics()
    metrics.inc("k1")
    metrics.inc("k1", 2)
    metrics.observe_duration("d1", 0.1)
    metrics.observe_duration("d1", 0.2)

    snap = metrics.snapshot()
    assert snap["counters"]["k1"] == 3
    assert "d1" in snap["duration"]
    assert snap["duration"]["d1"]["count"] == 2
    assert snap["duration"]["d1"]["max_s"] >= 0.2
    assert snap["rates_60s"]["k1"] > 0


def test_reliability_metrics_reset_clears_all() -> None:
    metrics = ReliabilityMetrics()
    metrics.inc("k1")
    metrics.observe_duration("d1", 0.3)
    metrics.reset()

    snap = metrics.snapshot()
    assert snap["counters"] == {}
    assert snap["duration"] == {}
    assert snap["rates_60s"] == {}
