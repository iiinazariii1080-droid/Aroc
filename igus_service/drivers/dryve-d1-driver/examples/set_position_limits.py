"""Example: Setting software position limits for the drive.

This example demonstrates how to set software position limits (0-120 cm)
in the drive hardware. The limits are enforced by the drive firmware and
will prevent movement beyond the specified range.

Note: The position values are in drive units. If your drive is configured
to use centimeters, then 0 cm = 0 and 120 cm = 120 (or 12000 if using 0.01 mm units).
Adjust the values according to your drive's configuration.
"""

import asyncio
import os
from drivers.dryve_d1 import DryveD1, DryveD1Config
from drivers.dryve_d1.config import DriveConfig, ConnectionConfig, MotionLimits


async def main() -> None:
    # Configure connection
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    port = int(os.getenv("DRYVE_PORT", "501"))
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))
    
    connection = ConnectionConfig(host=host, port=port, unit_id=unit_id)
    
    # Configure software position limits (0 - 120000 drive units)
    # Default limits are 0-120000, but you can override them if needed
    limits = MotionLimits(
        min_position_limit=0,       # Minimum position: 0 drive units
        max_position_limit=120000,  # Maximum position: 120000 drive units
    )
    
    drive_config = DriveConfig(connection=connection, limits=limits)
    config = DryveD1Config(drive=drive_config)
    
    drive = DryveD1(config=config)
    
    try:
        # Connect to drive (limits will be set automatically if configured)
        await drive.connect()
        print("Connected to drive")
        
        # Verify limits were set
        min_limit, max_limit = await drive.get_position_limits()
        print(f"Software position limits: {min_limit} - {max_limit} (drive units)")
        
        # Alternatively, you can set limits manually after connection:
        # await drive.set_position_limits(min_position=0, max_position=120)
        
        # Example: Try to move to a position within limits
        current_pos = await drive.get_position()
        print(f"Current position: {current_pos} (drive units)")
        
        # Move to a safe position (e.g., 50 cm)
        target_pos = 50
        if min_limit <= target_pos <= max_limit:
            print(f"Moving to position {target_pos}...")
            await drive.move_to_position(
                target_position=target_pos,
                velocity=2000,
                accel=5000,
                decel=5000,
            )
            print("Move completed successfully")
        else:
            print(f"Target position {target_pos} is outside limits [{min_limit}, {max_limit}]")
        
    finally:
        await drive.close()


if __name__ == "__main__":
    asyncio.run(main())

