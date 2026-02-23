"""Scenario A: Protocol stability test.

Tests that the Modbus TCP connection remains stable during repeated reads.
Performs 100 cycles of READ statusword + position with 50ms intervals.
Checks: no disconnects, valid responses, latency metrics.
"""

import asyncio
import time

import pytest

from drivers.dryve_d1.od.indices import ODIndex
from test_utils.metrics import TestMetrics
from test_utils.logging import TestLogger
from test_utils.config import TestConfig


@pytest.mark.asyncio
async def test_protocol_stability(
    drive, test_config: TestConfig
) -> None:
    """Test protocol stability with repeated reads.

    PASS criteria:
    - Connection never closes
    - All responses are valid (correct MBAP length)
    - No exceptions/timeouts during reads
    - Metrics: avg_latency, p95_latency recorded
    """
    logger = TestLogger("test_protocol_stability")
    metrics = TestMetrics(max_samples=test_config.protocol_stability_cycles)

    logger.log_stage("start", cycles=test_config.protocol_stability_cycles)

    # Track initial connection state
    initial_connected = drive.is_connected
    assert initial_connected, "Drive must be connected before test"

    cycles = test_config.protocol_stability_cycles
    interval_s = test_config.protocol_stability_interval_s

    for i in range(cycles):
        cycle_start = time.time()

        try:
            # Read statusword (0x6041)
            sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
            metrics.record_statusword(sw)
            logger.log_statusword(sw)

            # Read position (0x6064)
            pos = await drive.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE), 0)
            metrics.record_position(pos)
            logger.log_position(pos)

            # Check connection state
            is_connected = drive.is_connected
            if not is_connected:
                metrics.record_disconnect()
                logger.log_stage("disconnect_detected", cycle=i)

            # Record latency (time for both reads)
            cycle_latency = time.time() - cycle_start
            metrics.record_latency(cycle_latency)

        except Exception as e:
            logger.log_stage("error", cycle=i, error=str(e))
            raise

        # Wait for next cycle
        if i < cycles - 1:
            await asyncio.sleep(interval_s)

    logger.log_stage("complete", cycles=cycles)

    # Assertions
    assert (
        metrics.disconnect_count == 0
    ), f"Expected 0 disconnects, got {metrics.disconnect_count}"

    # Check that we got valid responses (latency recorded means no exception)
    assert (
        len(metrics.latencies) == cycles
    ), f"Expected {cycles} latency samples, got {len(metrics.latencies)}"

    # Log metrics
    avg_lat = metrics.avg_latency()
    p95_lat = metrics.p95_latency()
    logger.log_stage(
        "metrics",
        disconnect_count=metrics.disconnect_count,
        avg_latency_ms=avg_lat * 1000 if avg_lat else None,
        p95_latency_ms=p95_lat * 1000 if p95_lat else None,
    )

    logger.log_summary()

    # Final assertion: connection should still be valid
    assert drive.is_connected, "Drive should remain connected after test"

