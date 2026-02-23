#!/usr/bin/env python3
import asyncio
import os
import sys
import time

# Add src directory to path so we can import dryve_d1
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, "src"))

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig


async def main() -> None:
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))  # Default is 0 for Modbus TCP gateway

    connection = ConnectionConfig(host=host, unit_id=unit_id)
    drive_config = DriveConfig(connection=connection)
    cfg = DryveD1Config(drive=drive_config)

    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        await drive.fault_reset()
        await drive.enable_operation()

        # Simulated "button hold": jog for ~2 seconds with keepalive.
        vel = int(os.getenv("JOG_VELOCITY", "2000"))  # device units
        ttl_ms = int(os.getenv("JOG_TTL_MS", "200"))
        keepalive_period_s = float(os.getenv("JOG_KEEPALIVE_S", "0.05"))

        print(f"Jog start: velocity={vel}, ttl_ms={ttl_ms}")
        await drive.jog_start(velocity=vel, ttl_ms=ttl_ms)

        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            await drive.jog_update(velocity=vel, ttl_ms=ttl_ms)
            await asyncio.sleep(keepalive_period_s)

        print("Jog stop")
        await drive.jog_stop()
    finally:
        await drive.close()


if __name__ == "__main__":
    asyncio.run(main())
