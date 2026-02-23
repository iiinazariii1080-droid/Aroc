from __future__ import annotations

import re
from typing import Any, Dict

from app.core.settings import get_settings
from app.services.system import run


CTRL_MAP = {
    "exposure_auto": "exposure_auto",
    "exposure_absolute": "exposure_absolute",
    "gain": "gain",
    "white_balance_temperature_auto": "white_balance_automatic",
    "white_balance_temperature": "white_balance_temperature",
    "focus_auto": "focus_auto",
    "focus_absolute": "focus_absolute",
}


def list_v4l2_modes(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    output = run(["v4l2-ctl", "-d", device, "--list-formats-ext"], timeout=8)
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


def is_supported(mode_list: Dict[str, Any], width: int, height: int, fps: int) -> bool:
    for mode in mode_list["modes"]:
        if mode["width"] == width and mode["height"] == height and fps in mode["fps"]:
            return True
    return False


def v4l2_current(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    fmt = run(["v4l2-ctl", "-d", device, "--get-fmt-video"])
    prm = run(["v4l2-ctl", "-d", device, "--get-parm"])
    fmt_match = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", fmt)
    width, height = (int(fmt_match.group(1)), int(fmt_match.group(2))) if fmt_match else (None, None)
    pix_match = re.search(r"Pixel\s*Format\s*:\s*'([A-Z0-9]+)'", fmt)
    pix_fmt = pix_match.group(1) if pix_match else None
    fps_match = re.search(r"Frames per second\s*:\s*([\d.]+)", prm)
    fps = int(round(float(fps_match.group(1)))) if fps_match else None
    return {"width": width, "height": height, "fps": fps, "pixfmt": pix_fmt}


def list_v4l2_ctrls(dev: str | None = None) -> Dict[str, Any]:
    device = dev or get_settings().camera_device
    output = run(["v4l2-ctl", "-d", device, "--list-ctrls"], timeout=5)
    controls = {}
    for line in output.splitlines():
        match = re.match(
            r"^(\w[\w\-]*)\s+\((\w+)\)\s*:\s*min=([-0-9]+)\s+max=([-0-9]+)\s+step=([-0-9]+)\s+default=([-0-9]+)\s+value=([-0-9]+)",
            line.strip(),
        )
        if not match:
            fallback = re.match(r"^(\w[\w\-]*)\s+\((\w+)\)\s*:\s*(.*)$", line.strip())
            if fallback:
                name, typ, tail = fallback.groups()
                controls[name] = {"type": typ, "raw": tail}
            continue
        name, typ, vmin, vmax, step, default, value = match.groups()
        controls[name] = {
            "type": typ,
            "min": int(vmin),
            "max": int(vmax),
            "step": int(step),
            "default": int(default),
            "value": int(value),
        }
    return {"device": device, "controls": controls}


def apply_controls(values: Dict[str, int | bool]) -> Dict[str, int | bool]:
    if not values:
        return {}
    device = get_settings().camera_device
    args = []
    for key, value in values.items():
        if key not in CTRL_MAP:
            raise ValueError(f"unsupported control {key}")
        normalized = 1 if value is True else 0 if value is False else int(value)
        args.append(f"{CTRL_MAP[key]}={normalized}")
    if args:
        run(["v4l2-ctl", "-d", device, "--set-ctrl", ",".join(args)], timeout=8)
    return values

