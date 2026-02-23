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
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))  # Default is 0 for Modbus TCP gateway

    print(f"Connecting to {host}:501 (unit_id={unit_id})...")
    connection = ConnectionConfig(host=host, unit_id=unit_id)
    drive_config = DriveConfig(connection=connection)
    cfg = DryveD1Config(drive=drive_config)

    drive = DryveD1(config=cfg)
    try:
        await drive.connect()
        print("Connected successfully!")
    except (TimeoutError, ConnectionError, OSError) as e:
        print(f"Connection failed: {e}")
        print(f"\nTroubleshooting:")
        print(f"  - Verify the device is powered on and connected to the network")
        print(f"  - Check that the IP address {host} is correct")
        print(f"  - Try pinging the device: ping {host}")
        print(f"  - Set DRYVE_HOST environment variable to the correct IP:")
        print(f"    $env:DRYVE_HOST='<your-device-ip>'")
        raise
    try:
        await drive.fault_reset()
        await drive.enable_operation()

        # Homing (method depends on your mechanical setup; edit in config defaults if needed)
        # Can be skipped for simulators or if homing is not needed
        skip_homing = os.getenv("SKIP_HOMING", "false").lower() in ("true", "1", "yes")
        if not skip_homing:
            print("Homing...")
            try:
                await drive.home()
                print("Homing completed")
            except TimeoutError as e:
                print(f"Homing timeout: {e}")
                print("Skipping homing and continuing with position move...")
        else:
            print("Skipping homing (SKIP_HOMING is set)")

        # Move to a target position
        target = int(os.getenv("TARGET_POSITION", "10000"))
        velocity = int(os.getenv("PROFILE_VELOCITY", "5000"))
        accel = int(os.getenv("PROFILE_ACCEL", "10000"))
        decel = int(os.getenv("PROFILE_DECEL", "10000"))
        print(f"Move to position: {target}")
        await drive.move_to_position(
            target_position=target,
            velocity=velocity,
            accel=accel,
            decel=decel,
        )
        print("Done")
    finally:
        await drive.close()


if __name__ == "__main__":
    asyncio.run(main())
