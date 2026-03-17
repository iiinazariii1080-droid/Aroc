#!/usr/bin/env python3
"""Firmware-level hardware reset for Intel RealSense D435/D435i.

Sends a vendor-specific USB control transfer that reboots the camera's
internal firmware.  This fixes the stuck VIDIOC_S_FMT errno=5 state
that a simple `usbreset` or modprobe cycle cannot recover from.

Usage:
    python3 hw_reset_realsense.py          # reset + wait + verify
    python3 hw_reset_realsense.py --wait 8 # custom wait (default 6s)
    python3 hw_reset_realsense.py --quiet  # suppress output
"""

import sys
import time
import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="RealSense firmware-level hardware reset")
    parser.add_argument("--wait", type=int, default=6, help="Seconds to wait after reset for re-enumeration")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[hw-reset] {msg}", flush=True)

    try:
        import pyrealsense2 as rs
    except ImportError:
        log("ERROR: pyrealsense2 not installed")
        return 1

    ctx = rs.context()
    devs = ctx.query_devices()
    if len(devs) == 0:
        log("no RealSense device found")
        return 1

    dev = devs[0]
    name = dev.get_info(rs.camera_info.name)
    serial = dev.get_info(rs.camera_info.serial_number)
    log(f"found {name} (serial {serial})")
    log("sending hardware_reset() ...")
    dev.hardware_reset()
    log(f"waiting {args.wait}s for re-enumeration ...")
    time.sleep(args.wait)

    # verify device is back
    ctx2 = rs.context()
    devs2 = ctx2.query_devices()
    if len(devs2) == 0:
        log("WARNING: device not found after reset")
        return 2

    log(f"device back: {devs2[0].get_info(rs.camera_info.name)} (serial {devs2[0].get_info(rs.camera_info.serial_number)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
