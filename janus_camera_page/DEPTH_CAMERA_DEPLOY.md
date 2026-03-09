# Depth Camera Node — Deployment & Operations Guide

> Host: `192.168.1.55` (Raspberry Pi 5)  
> SSH: `boris` — password in `/etc/robot/camera-secrets.env`  
> Quick access: `ssh boris@192.168.1.55`

---

## Architecture Overview

The depth camera node runs a **RealSense D435** camera with three simultaneous streams:

```
RealSense D435
    │
    ├── Color (RGB) ────► color.fifo ──► rtp-rgb(ffmpeg) ──► Janus MP 1305
    ├── Depth (Z16→RGB) ► depth.fifo ──► rtp-depth(ffmpeg) ► Janus MP 1306
    └── IR (disabled)                                       (Janus MP 1307 — stale)
    │
    └── depth array (float32) ──► CameraService (HTTP :8000/depth)
```

### Service Stack (systemd)

| Service | Port | Description |
|---------|------|-------------|
| `realsense-mux.service` | 8000 | Python RealSense pipeline + depth HTTP API |
| `rtp-rgb.service` | 5002 (RTP) | ffmpeg: color FIFO → H264 → Janus |
| `rtp-depth.service` | 5004 (RTP) | ffmpeg: depth FIFO → H264 → Janus |
| `janus.service` | 8088 (HTTP), 8188 (WS) | Janus WebRTC gateway |
| `janus-camera-page.service` | 8900 | FastAPI web UI + depth proxy + watchdog |

### Data Flow for Depth Reading

```
Browser click (x%, y%)
    → GET /api/v1/depth_camera/depth?x=50&y=50   (port 8900, FastAPI)
    → proxy → GET http://localhost:8000/depth?x=50&y=50  (realsense-mux)
    → CameraService.get_depth(x/100, y/100)
    → depth_m[row, col] from rotated float32 array
    → {"type":"depth", "x":50.0, "y":50.0, "depth": 0.376}
```

**CRITICAL**: The depth float32 array and the video stream MUST have the same rotation.
Both use `rotate="cw"` (90° clockwise) applied in `realsense_mux.py`.
Do NOT add rotation in ffmpeg (rtp-depth) — it rotates only the video but not the
depth array, causing coordinate mismatch.

---

## Systemd Configuration

### Base Services

Located in `/etc/systemd/system/`:

- `janus-camera-page.service` — FastAPI app
- `realsense-mux.service` — RealSense pipeline
- `rtp-rgb.service` — ffmpeg for color stream
- `rtp-depth.service` — ffmpeg for depth stream
- `janus.service` — Janus WebRTC gateway
- `realsense-failsafe.service` — USB reset on pipeline failure
- `usb-reset-realsense.service` — USB device reset helper

### Drop-in Overrides

```
/etc/systemd/system/
├── janus-camera-page.service.d/
│   ├── camera-role.conf       # CAM_TYPE=depth_camera, JANUS_MOUNT_ID=1306, etc.
│   └── watchdog.conf          # CAM_SERVICE=realsense-mux, watchdog params
├── rtp-depth.service.d/
│   └── failsafe.conf          # OnFailure, Restart=always
├── rtp-rgb.service.d/
│   ├── failsafe.conf          # OnFailure, Restart=always
│   └── override.conf          # ExecStartPre await, AppArmor
├── realsense-mux.service.d/
│   ├── failsafe.conf          # OnFailure, Restart=always
│   └── await-device.conf      # Device wait
├── janus.service.d/
│   └── disable-sandbox.conf   # Janus sandbox workaround
└── usb-reset-realsense.service.d/
    └── timeout.conf           # Stop timeout
```

### camera-role.conf

```ini
[Service]
Environment=CAM_TYPE=depth_camera
Environment=JANUS_MOUNT_ID=1306
Environment=CAM_ENV_PATH=/etc/robot/cam-depth.env
Environment=CAM_ENV_LOCK_PATH=/tmp/cam-depth.env.lock
Environment=CAM_DEVICE=/dev/cam-depth
Environment=PYTHONPATH=/home/boris/robot
```

### watchdog.conf

```ini
[Service]
Environment=CAM_SERVICE=realsense-mux.service
Environment=CAM_WATCHDOG=1
Environment=CAM_SNAPSHOT_WATCHDOG=0
Environment=CAM_WATCHDOG_INTERVAL=8
Environment=CAM_WATCHDOG_STALE_MS=8000
```

### Sudoers

```
/etc/sudoers.d/
├── cam-watchdog                    # passwordless: sudo systemctl restart realsense-mux.service
├── janus-camera-page-systemctl     # passwordless: restart rtp-rgb, rtp-depth, usb-reset, realsense-failsafe
└── janus-ctl                       # Janus control
```

---

## Web Views

| URL | Stream | Description |
|-----|--------|-------------|
| `/api/v1/depth_camera/color_view.html` | MP 1305 (RGB) | Color stream, joystick OFF |
| `/api/v1/depth_camera/depth_view.html` | MP 1306 (Depth) | Depth colormap + gripper reticle, joystick OFF |
| `/api/v1/depth_camera/ir_view.html` | MP 1307 (IR) | IR stream (currently stale — IR disabled in pipeline) |

### Joystick Behavior

The depth camera node has **no relay server** (no `textroom_relay.py` running).
The joystick service is automatically disabled:

- `depth_view.html`: has `data-joystick-mode="off"` in template
- `color_view.html`: `_render_template_response()` replaces `"always"` → `"off"` when `CAM_TYPE=depth_camera`
- Variant views (depth/IR via `_render_color_view_variant()`): explicit `joystick=False`

This prevents 502 errors from `/relay/time` and `/relay/pong` endpoints which proxy
to `http://127.0.0.1:9000` (a relay that doesn't exist on this node).

---

## realsense_mux.py

The pipeline script at `/home/boris/robot/janus_camera_page/realsense_mux.py`:

### Hardcoded Parameters

```python
rotate = "cw"           # 90° clockwise rotation for ALL streams
color_idx = 90          # RealSense mode index for color
depth_idx = 18          # RealSense mode index for depth
ir_idx = -1             # IR disabled
```

### FIFOs

- `/run/realsense/color.fifo` — RGB24 frames for rtp-rgb
- `/run/realsense/depth.fifo` — Colorized depth (RGB24) for rtp-depth

### HTTP API (port 8000)

- `GET /depth?x=<0..100>&y=<0..100>` — Depth value in meters at normalized coordinates
- `GET /color_frame?format=json|raw` — Latest RGB frame from D435 color sensor

### Rotation Architecture

All streams share the same `rotate="cw"`:

1. **Color frames**: `rotate_img(img, "cw")` → written to `color.fifo`
2. **Depth visual**: `rotate_img(colorized, "cw")` → written to `depth.fifo`
3. **Depth array**: `rotate_img(z16_float32, "cw")` → stored in `CameraService._depth_m`

This ensures the depth array coordinates match the video exactly.

**DO NOT** add rotation filters (`hflip`, `vflip`, `transpose`) in ffmpeg services.
If rotation needs to change, modify ONLY `rotate` in `realsense_mux.py` `main()`.

---

## Janus Configuration

- Config path: `/opt/janus/etc/janus/janus.jcfg`
- Session timeout: `30` seconds
- Streaming plugin: 3 mountpoints (1305, 1306, 1307)

### Mountpoints

| ID | Description | RTP Port | Status |
|----|-------------|----------|--------|
| 1305 | rgb-ccw | 5002 | Active (color stream) |
| 1306 | depth | 5004 | Active (depth colormap) |
| 1307 | IR | 5006 | Defined but stale (IR pipeline disabled) |

---

## Recovery & Failsafe Chain

```
Stream stalls
    → janus-camera-page watchdog detects stale Janus mountpoint
    → sudo systemctl restart realsense-mux.service
    → realsense-mux.service restarts
    → rtp-rgb & rtp-depth restart (Requires= dependency + FIFO reopen)
    
Pipeline crash
    → realsense-mux.service: Restart=always
    → rtp-*.service: Restart=always + OnFailure=realsense-failsafe.service
    
USB device lost
    → realsense-failsafe.service → usb-reset-realsense.service → device re-enumerate
```

---

## Differences from Color Camera Node (192.168.1.10)

| Aspect | Color Camera (.10) | Depth Camera (.55) |
|--------|-------------------|-------------------|
| CAM_TYPE | `color_camera` (default) | `depth_camera` (drop-in) |
| Janus Mount | 1305 only | 1305 + 1306 (+ 1307 stale) |
| Pipeline | V4L2 USB camera | RealSense D435 via realsense_mux.py |
| Joystick | Enabled (relay on server) | Disabled (no relay) |
| TextRoom | Enabled | Disabled |
| Depth API | N/A | `/depth?x=&y=` → meters |
| ICE Policy | `relay` (TURN only) | `relay` (hardcoded for depth) |
| Watchdog target | rtp-rgb@cam-rgb.service | realsense-mux.service |

---

## Troubleshooting

### Check all services
```bash
for s in janus-camera-page rtp-depth rtp-rgb realsense-mux janus; do
  printf "%-25s " "$s:"; systemctl is-active $s
done
```

### Check Janus stream health
```bash
python3 -c "
import requests
r = requests.post('http://localhost:8088/janus', json={'janus':'create','transaction':'t'})
sid = r.json()['data']['id']
r2 = requests.post(f'http://localhost:8088/janus/{sid}', json={'janus':'attach','plugin':'janus.plugin.streaming','transaction':'t2'})
hid = r2.json()['data']['id']
for mp in [1305, 1306]:
    r3 = requests.post(f'http://localhost:8088/janus/{sid}/{hid}', json={'janus':'message','body':{'request':'info','id':mp},'transaction':f'i{mp}'})
    info = r3.json()['plugindata']['data']['info']
    m = info.get('media',[{}])[0]
    print(f'  MP {mp}: age_ms={m.get(\"age_ms\",\"N/A\")}')
"
```

### Test depth reading
```bash
curl "http://localhost:8900/api/v1/depth_camera/depth?x=50&y=50"
# Expected: {"type":"depth","x":50.0,"y":50.0,"depth":0.376}
```

### View logs
```bash
journalctl -u janus-camera-page -f
journalctl -u realsense-mux -f
journalctl -u rtp-depth -f
```

### Force restart everything
```bash
sudo systemctl restart realsense-mux  # cascades to rtp-* 
sudo systemctl restart janus-camera-page
```

---

## Files on This Node

### Templates (served to browser)

| File | Purpose | Used? |
|------|---------|-------|
| `color_view.html` | RGB stream player | ✅ Stream 1305 |
| `depth_view.html` | Depth stream + gripper reticle | ✅ Stream 1306 |
| `gripper_reticle.js` | Depth crosshair overlay | ✅ Loaded by depth_view.html |
| `depth_features.js` | Depth UI features | ✅ Available via API |
| `janus.js` | Janus WebRTC client lib | ✅ Core dependency |
| `gamepaddriver.js` | Gamepad input driver | ❌ Not used (joystick off) |
| `gamepad_config.json` | Gamepad axis mapping | ❌ Not used (joystick off) |
| `streamer.js` | Compatibility shim (loads player/) | ⚠️ Legacy, not referenced by current templates |
| `streamer_v2.js` | Unknown (exists only on remote) | ⚠️ Likely unused |
| `dump` | Old WebRTC stats dump (191KB) | ❌ Debug garbage, safe to delete |
| `review.md` | Code review notes | ⚠️ Reference only |
| `player/` | Clean-architecture player stack | ✅ Core |
| `tests/` | Player unit tests | ✅ Dev only |

### Safely deletable files
```bash
rm -f templates/dump              # old stats dump
rm -f templates/streamer_v2.js    # unused legacy
```
