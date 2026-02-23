#!/usr/bin/env python3
import asyncio
import os

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.defaults import default_driver_config


async def main() -> None:
    host = os.getenv("DRYVE_HOST", "192.168.1.10")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "1"))

    cfg: DryveD1Config = default_driver_config(host=host, unit_id=unit_id)

    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        await drive.fault_reset()
        await drive.enable_operation()

        # Homing (method depends on your mechanical setup; edit in config defaults if needed)
        print("Homing...")
        await drive.home()

        # Move to a target position
        target = int(os.getenv("TARGET_POSITION", "10000"))
        print(f"Move to position: {target}")
        await drive.move_to_position(target_position=target)
        print("Done")
    finally:
        await drive.close()


if __name__ == "__main__":
    asyncio.run(main())
