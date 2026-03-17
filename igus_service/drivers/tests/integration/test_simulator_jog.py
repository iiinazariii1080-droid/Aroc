import asyncio
import os
import time

import pytest

from drivers.dryve_d1.api.drive import DryveD1
from drivers.dryve_d1.config.defaults import default_driver_config


HOST = os.getenv("DRYVE_HOST")
if not HOST:
    pytest.skip("Set DRYVE_HOST to run simulator integration tests", allow_module_level=True)

PORT = int(os.getenv("DRYVE_PORT", "501"))
UNIT_ID = int(os.getenv("DRYVE_UNIT_ID", "0"))

JOG_VELOCITY = int(os.getenv("DRYVE_JOG_VELOCITY", "2000"))
JOG_TTL_MS = int(os.getenv("DRYVE_JOG_TTL_MS", "200"))
JOG_KEEPALIVE_S = float(os.getenv("DRYVE_JOG_KEEPALIVE_S", "0.05"))
JOG_DURATION_S = float(os.getenv("DRYVE_JOG_DURATION_S", "2.0"))


@pytest.mark.asyncio
async def test_simulator_jog_back_and_forth() -> None:
    cfg = default_driver_config(host=HOST, port=PORT, unit_id=UNIT_ID)
    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        drive._jog_shutdown_delay_s = 0  # type: ignore[attr-defined]
        await drive.fault_reset()
        await drive.enable_operation()

        sw_before = await drive.read_u16(0x6041, 0)
        assert 0 <= sw_before <= 0xFFFF

        async def jog_for(velocity: int) -> None:
            await drive.jog_start(velocity=velocity, ttl_ms=JOG_TTL_MS)
            t0 = time.monotonic()
            while time.monotonic() - t0 < JOG_DURATION_S:
                await drive.jog_update(velocity=velocity, ttl_ms=JOG_TTL_MS)
                await asyncio.sleep(JOG_KEEPALIVE_S)
            await drive.jog_stop()
            await asyncio.sleep(0)

        await jog_for(JOG_VELOCITY)
        await jog_for(-JOG_VELOCITY)

        sw_after = await drive.read_u16(0x6041, 0)
        assert 0 <= sw_after <= 0xFFFF
    finally:
        try:
            await drive.jog_stop()
        except Exception:
            pass
        try:
            await drive.stop()
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while True:
            try:
                if not await drive.is_moving():
                    break
            except Exception:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        try:
            drive._cancel_jog_shutdown_timer()  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        except Exception:
            pass
        await drive.close()
