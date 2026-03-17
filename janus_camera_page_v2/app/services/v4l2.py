from __future__ import annotations

import re
from typing import Any, Dict

from app.core.settings import get_settings
from app.utils.process import run_cmd



def list_v4l2_modes(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    output = run_cmd(["v4l2-ctl", "-d", device, "--list-formats-ext"], timeout=8)
    blocks = output.split("Type: Video Capture")
    modes = []
    for block in blocks:
        if "'YUYV'" not in block:
            continue
        for match in re.finditer(
            r"Size:\s+Discrete\s+(\d+)x(\d+)(.*?)(?=Size:|$)", block, re.S
        ):
            width, height = int(match.group(1)), int(match.group(2))
            tail = match.group(3)
            fps = set()
            for fps_match in re.finditer(
                r"Interval:\s+Discrete\s+([0-9.]+)s\s+\(([\d.]+)\s+fps\)", tail
            ):
                fps.add(int(round(float(fps_match.group(2)))))
            modes.append({"width": width, "height": height, "fps": sorted(fps, reverse=True)})
    return {"pixel_format": "YUYV", "device": device, "modes": modes}



