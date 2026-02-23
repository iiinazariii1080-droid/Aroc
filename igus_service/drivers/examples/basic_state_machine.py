#!/usr/bin/env python3
import asyncio
import os

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.defaults import default_driver_config


async def main() -> None:
    # Configure connection via env vars or edit below.
    host = os.getenv("DRYVE_HOST", "192.168.1.10")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "1"))

    cfg: DryveD1Config = default_driver_config(host=host, unit_id=unit_id)

    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        # Typical sequence: clear fault (if any) -> enable operation
        await drive.fault_reset()
        await drive.enable_operation()

        sw = await drive.read_u16(0x6041, 0)
        print(f"Statusword: 0x{sw:04X}")
    finally:
        await drive.close()


if __name__ == "__main__":
    asyncio.run(main())
