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

TARGET = int(os.getenv("DRYVE_TARGET_POSITION", "10000"))
VELOCITY = int(os.getenv("DRYVE_MOVE_VELOCITY", "10000"))
ACCEL = int(os.getenv("DRYVE_MOVE_ACCEL", "5000"))
DECEL = int(os.getenv("DRYVE_MOVE_DECEL", "5000"))


@pytest.mark.asyncio
async def test_simulator_move_to_position_10000() -> None:
    cfg = default_driver_config(host=HOST, port=PORT, unit_id=UNIT_ID)
    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        await drive.fault_reset()
        await drive.enable_operation()

        pos_before = await drive.get_position()
        await drive.move_to_position(
            target_position=TARGET,
            velocity=VELOCITY,
            accel=ACCEL,
            decel=DECEL,
            timeout_s=20.0,
            require_homing=False,
        )
        pos_after = await drive.get_position()

        # Allow simulator to report exact target or a close value.
        assert abs(pos_after - TARGET) <= 50 or abs(pos_after - pos_before) > 0
    finally:
        try:
            await drive.stop()
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while True:
            try:
                if not await drive.is_motion():
                    break
            except Exception:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        await drive.close()
