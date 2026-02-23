#!/usr/bin/env python3
import asyncio
import os
import sys

# Add src directory to path so we can import dryve_d1
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, "src"))

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig


async def main() -> None:
    # Configure connection via env vars or edit below.
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))  # Default is 0 for Modbus TCP gateway

    connection = ConnectionConfig(host=host, unit_id=unit_id)
    drive_config = DriveConfig(connection=connection)
    cfg = DryveD1Config(drive=drive_config)

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
