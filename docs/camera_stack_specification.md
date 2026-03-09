# Dual-Stack Camera System Specification

**Version:** 1.8  
**Date:** 2026-03-06  
**Scope:** As-built architecture — RealSense USB hardware to browser pixel rendering  
**Nodes:** Color (192.168.1.10), Depth (192.168.1.55)

---

## Table of Contents

1. [Hardware Layer](#1-hardware-layer)
2. [Driver & Capture](#2-driver--capture)
3. [Encoding](#3-encoding)
4. [Janus WebRTC](#4-janus-webrtc)
5. [NAT Traversal](#5-nat-traversal)
6. [Firewall & QoS](#6-firewall--qos)
7. [FastAPI Server](#7-fastapi-server)
8. [Depth API & Contract](#8-depth-api--contract)
9. [Browser Player](#9-browser-player)
10. [Watchdog & Reconnection](#10-watchdog--reconnection)
11. [3D Viewer](#11-3d-viewer)
12. [FDIR](#12-fdir)
13. [Systemd Map](#13-systemd-map)
14. [SLOs](#14-slos)
15. [Reference Tables](#15-reference-tables)
16. [Observability Stack](#16-observability-stack)
17. [Synthetic Probes & Fault Injection](#17-synthetic-probes--fault-injection)

---

## 1. Hardware Layer

### Camera Hardware

| Attribute       | Color Node (.10)                | Depth Node (.55)                |
|-----------------|----------------------------------|----------------------------------|
| Camera          | Intel RealSense D435i            | Intel RealSense D435             |
| USB             | USB 3.0 (currently 2.0 adapter)  | USB 3.0                         |
| Compute         | Raspberry Pi 5                   | Raspberry Pi 5                   |
| Power           | 5V/5A (official PSU)             | 5V/3A (insufficient — peripheral power limited) |
| OS              | Ubuntu 25.10 (aarch64)           | Ubuntu 25.10 (aarch64)           |
| Kernel          | 6.17.0-1003-raspi                | 6.17.0-1003-raspi                |
| Storage         | 58 GB (13% used)                 | 58 GB                            |
| OOB Access      | SSH (LAN)                        | SSH (LAN) + Tailscale VPN        |

### Network Topology

```
                        INTERNET
                           │
                    ┌──────┴──────┐
                    │  TURN VPS   │
                    │ 82.165.177.194
                    │  coturn     │
                    │  3478/443   │
                    └──────┬──────┘
                           │
                    ISP / public IP
                    87.156.23.54 (dynamic)
                           │
              ┌────────────┴────────────┐
              │  Color Node (.10)       │
              │  wlan0: 192.168.10.112  │  ← WAN uplink (WiFi)
              │  br0:   192.168.1.10    │  ← LAN bridge
              │  ip_forward=1           │  ← Gateway
              │  Docker host            │
              └────────────┬────────────┘
                    LAN 192.168.1.0/24
                           │
              ┌────────────┴────────────┐
              │  Depth Node (.55)       │
              │  wlan0: 192.168.1.55    │  ← WiFi to LAN
              │  default gw: .10        │
              │  tailscale0: VPN OOB    │
              └─────────────────────────┘

  Browser paths
    ├── LAN:      ws://192.168.1.10:8188/janus-ws  (direct WS to Janus)
    ├── Internet: wss://api.techvisioncloud.pl/api/v1/color_camera/janus-ws
    │             Cloudflare → frontend :8401 → API Gateway :8201 → FastAPI :8900 → ws://127.0.0.1:8188/janus-ws
    ├── Media:    TURN relay (82.165.177.194) — ICE/media plane only, not signaling
    └── Depth:    /api/v1/depth_camera/* reverse proxy on .10:8900 (including /janus-ws)
```

### Component Roles (strict separation)

Each infrastructure component has **one** well-defined role.  Mixing
these roles leads to incorrect assumptions about failover and security.

| Component | Role | Owns | Does NOT own |
|-----------|------|------|--------------|
| **Cloudflare Tunnel** | Published UI / API / WSS ingress (control plane) | HTTPS termination, DNS routing, DDoS protection, edge caching | Media relay, ICE candidates, TURN |
| **TURN VPS** (82.165.177.194) | WebRTC media relay | UDP/TCP/TLS relay allocations, ephemeral HMAC credential **validation**, bandwidth cap (10 Mbps) | Signaling, HTML serving, API routing, credential generation |
| **Janus Gateway** | Media broker / WebRTC termination | RTP ingest, SDP negotiation, ICE, DTLS, streaming plugin mounts | HTTP API serving (proxied by FastAPI), recovery policy |
| **FastAPI (.10)** | Gateway + primary control node | Health probes, recovery ladder, metrics, admin API, reverse proxy to .55, ephemeral TURN credential **issuance** (`generate_turn_credentials()`) | Direct media handling |
| **Depth Node (.55)** | Subordinate sensor node | Local RTP capture, local Janus, depth pipeline | WAN routing, Cloudflare, TURN management |

**Failure isolation rules:**
- Cloudflare Tunnel down → UI unreachable externally, but LAN streaming and TURN relay continue.
- TURN VPS down → Remote viewers behind symmetric NAT lose media; LAN viewers and host-candidate viewers unaffected.
- Janus down → No media at all; FastAPI still serves health/admin; systemd + FDIR ladder escalate.
- .10↔.55 link down → Depth stream lost; color stream continues; system enters DEGRADED mode.

### Data Flow — One Frame, USB to Browser

**Color path (.10):**
```
D435i USB ──► uvcvideo ──► /dev/cam-rgb (V4L2 YUYV)
  ──► ffmpeg (V4L2 direct capture, libx264)
  ──► rtp://127.0.0.1:5004 (H.264 RTP)
  ──► Janus mount 1305 (ingest)
  ──► DTLS-SRTP (WebRTC ICE)
  ──► Browser <video> element
```

**Depth path (.55):**
```
D435 USB ──► pyrealsense2 (librealsense)
  ──► realsense_mux.py (CameraService)
  ──► /run/realsense/color.fifo  ──► ffmpeg ──► rtp://127.0.0.1:5003 ──► Janus 1305
  ──► /run/realsense/depth.fifo  ──► ffmpeg ──► rtp://127.0.0.1:5004 ──► Janus 1306
  ──► Janus DTLS-SRTP ──► Color node reverse proxy ──► Browser
```

---

## 2. Driver & Capture

### Side-by-Side Comparison

| Aspect              | Color (.10) — V4L2 Direct        | Depth (.55) — pyrealsense2 Mux     |
|---------------------|-----------------------------------|--------------------------------------|
| **Driver**          | `uvcvideo` kernel module          | `pyrealsense2` (librealsense)        |
| **Device**          | `/dev/cam-rgb` (udev symlink)     | USB enumeration by serial            |
| **Capture API**     | V4L2 (`ffmpeg -f v4l2`)           | `rs.pipeline().start(config)`        |
| **Pixel format**    | YUYV (V4L2 native)               | RGB24 (color), Z16 (depth)           |
| **Resolution**      | Configurable (640×480 default)    | 480×640 (portrait, post-rotation)    |
| **FPS**             | Configurable (30 default)         | 15 fps                               |
| **Rotation**        | None (landscape)                  | 90° CW (`np.rot90(arr, 3)`)          |
| **FIFO**            | None (V4L2 → ffmpeg direct)      | `/run/realsense/color.fifo`, `depth.fifo` |
| **Snapshot**        | ffmpeg `-snapshot_fps 1` output   | Not applicable (HTTP API)            |
| **HW reset**        | Not implemented                   | `device.hardware_reset()` on VIDIOC_S_FMT error, 6s wait, 2 retries |
| **Mode selection**  | V4L2 format negotiation           | `color_idx=90`, `depth_idx=18` (env-configurable) |
| **Config file**     | `/etc/robot/cam-rgb.env`          | Env vars `RS_COLOR_IDX`, `RS_DEPTH_IDX`, `RS_IR_IDX` |

### realsense_mux.py — Depth Capture Pipeline

**`run_pipeline()` parameters:**

| Parameter     | Value               | Purpose                          |
|---------------|---------------------|----------------------------------|
| `color_idx`   | 90 (env `RS_COLOR_IDX`)  | RealSense mode index (color)     |
| `depth_idx`   | 18 (env `RS_DEPTH_IDX`)  | RealSense mode index (depth)     |
| `ir_idx`      | -1 (env `RS_IR_IDX`)    | Disabled (skip IR stream)        |
| `color_fifo`  | `/run/realsense/color.fifo` | RGB24 → ffmpeg               |
| `depth_fifo`  | `/run/realsense/depth.fifo` | Colorized depth → ffmpeg     |
| `rotate`      | `"cw"`              | 90° clockwise rotation           |
| `depth_flip180` | `False`           | No additional flip               |

**CameraService class** (thread-safe via `threading.Lock`):

- `update_depth_from_z16(z16, scale_m_per_unit)` — stores depth as float32 meters with rotation/flipping
- `update_color_rgb(rgb)` — stores latest uint8 RGB24 color frame
- `get_depth(x_norm, y_norm)` → `(depth_val, i, j, W, H, timestamp)` — normalized coords [0..1]
- `get_depth_map()` → `{array, width, height, timestamp}` — full depth array
- Colorizer: `rs.colorizer()` converts Z16 → RGB24 pseudo-color for visualization FIFO

**ModeInfo dataclass:**
```
stream: str            # "color", "depth", "ir"
stream_type: rs.stream
stream_index: int
width, height, fps: int
format: rs.format
```

**Hardware reset logic:**
1. `VIDIOC_S_FMT` error (errno=5) triggers `device.hardware_reset()`
2. Wait 6 seconds for USB re-enumeration
3. Retry pipeline creation (max 2 attempts)
4. On exhaustion → service crash → systemd restart

**FIFO recovery:**
- `BrokenPipeError` on FIFO write (reader died) → reopen FIFO, retry
- Consecutive failure counter per channel (`color`, `depth`, `ir`) — tracks sequential reopen failures
- After 10 consecutive failures (`_FIFO_MAX_CONSECUTIVE_FAILURES`) → `RuntimeError` escalation → systemd restart
- On successful reopen, counter resets to 0
- Prevents pipeline crash when downstream ffmpeg restarts; prevents infinite silent failure loops

---

## 3. Encoding

### ffmpeg Pipelines

**Color node** — `rtp-rgb@cam-rgb.service` (V4L2 template):

```
ffmpeg -f v4l2 -input_format yuyv422 -video_size ${WIDTH}x${HEIGHT} \
  -framerate ${FPS} -i /dev/%i \
  -vf format=yuv420p \
  -c:v libx264 -preset ${PRESET} -tune ${TUNE} \
  -profile:v baseline -level:v 3.1 \
  -b:v ${BITRATE_KBPS}k -maxrate ${BITRATE_KBPS}k -bufsize $((BITRATE_KBPS*2))k \
  -g ${GOP} -keyint_min ${GOP} -sc_threshold 0 \
  -x264-params repeat-headers=1:bframes=0 \
  -an -f rtp "rtp://127.0.0.1:${PORT}?pkt_size=1200" \
  -vf "fps=1" -update 1 -q:v 5 ${SNAPSHOT_PATH}
```

Default env (`/etc/robot/cam-rgb.env`):

| Variable       | Default | Purpose                     |
|----------------|---------|------------------------------|
| `WIDTH`        | 640     | Frame width (pixels)         |
| `HEIGHT`       | 480     | Frame height (pixels)        |
| `FPS`          | 30      | Capture frame rate           |
| `BITRATE_KBPS` | 1800    | Target bitrate               |
| `PRESET`       | veryfast | x264 speed/quality tradeoff |
| `TUNE`         | zerolatency | Low-latency tuning        |
| `GOP`          | 30      | Keyframe interval (frames)   |
| `PORT`         | 5004    | RTP destination port         |
| `SNAPSHOT_FPS` | 1       | JPEG snapshot cadence        |

**Depth node — `rtp-rgb.service`** (color from realsense, not templated):

```
ffmpeg -f rawvideo -pixel_format rgb24 -video_size 480x640 -framerate 15 \
  -i /run/realsense/color.fifo \
  -vf format=yuv420p \
  -c:v libx264 -preset veryfast -tune zerolatency -profile:v baseline -level:v 3.1 \
  -b:v 1500k -maxrate 1500k -bufsize 3000k \
  -g 30 -keyint_min 30 -sc_threshold 0 \
  -x264-params repeat-headers=1:bframes=0 \
  -an -f rtp "rtp://127.0.0.1:5003?pkt_size=1200"
```

**Depth node — `rtp-depth.service`** (depth colormap):

```
ffmpeg -f rawvideo -pixel_format rgb24 -video_size 480x640 -framerate 15 \
  -i /run/realsense/depth.fifo \
  -vf format=yuv420p \
  -c:v libx264 -preset veryfast -tune zerolatency -profile:v baseline -level:v 3.1 \
  -b:v 1000k -maxrate 1000k -bufsize 2000k \
  -g 30 -keyint_min 30 -sc_threshold 0 \
  -x264-params repeat-headers=1:bframes=0 \
  -an -f rtp "rtp://127.0.0.1:5004?pkt_size=1200"
```

### Encoding Parameter Summary

| Parameter        | Color (.10)        | Depth RGB (.55)    | Depth Map (.55)    |
|------------------|--------------------|--------------------|--------------------|
| Input format     | V4L2 YUYV          | rawvideo RGB24     | rawvideo RGB24     |
| Resolution       | 640×480 (default)  | 480×640            | 480×640            |
| FPS              | 30 (default)       | 15                 | 15                 |
| Codec            | H.264 baseline 3.1 | H.264 baseline 3.1 | H.264 baseline 3.1 |
| Preset           | veryfast            | veryfast            | veryfast            |
| Tune             | zerolatency         | zerolatency         | zerolatency         |
| Bitrate          | 1800 kbps           | 1500 kbps           | 1000 kbps           |
| GOP              | 30                  | 30                  | 30                  |
| B-frames         | 0                   | 0                   | 0                   |
| Repeat headers   | Yes                 | Yes                 | Yes                 |
| Packet size      | 1200                | 1200                | 1200                |
| RTP dest         | 127.0.0.1:5004      | 127.0.0.1:5003      | 127.0.0.1:5004      |

---

## 4. Janus WebRTC

### Mount Points

| Mount ID | Node   | Description       | RTP Port | RTCP Port | Codec | Profile                     |
|----------|--------|-------------------|----------|-----------|-------|-----------------------------|
| 1305     | Color  | H.264 color cam   | 5004     | 5005      | h264  | 42e01f (baseline L3.1)      |
| 1305     | Depth  | RGB from D435     | 5003     | —         | h264  | 42e01f (baseline)           |
| 1306     | Depth  | Depth colormap    | 5004     | —         | h264  | 42e01f (baseline)           |
| 1307     | Depth  | IR (disabled)     | 5005     | —         | h264  | 42e01f (baseline)           |

All mount points: `videobufferkf=true`, `videosimulcast=false`, payload type 96.

FMTP: `packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1`

### Transport Ports

| Transport          | Port  | Interface | Both Nodes |
|--------------------|-------|-----------|------------|
| Janus REST API     | 8088  | 0.0.0.0 (LAN-only via fw) | Yes |
| Janus WebSocket    | 8188  | 0.0.0.0 (LAN-only via fw) | Yes |
| Janus WSS          | 8989  | 0.0.0.0   | Yes        |
| Janus Admin HTTP   | 7088  | 0.0.0.0   | Yes (LAN restricted) |
| RTP ingest range   | 5002–5120 | 127.0.0.1 | Yes     |
| ICE candidate range| 40000–41000 | 0.0.0.0 | Yes     |

### Core Configuration (janus.jcfg)

| Parameter               | Color Node        | Depth Node        |
|--------------------------|-------------------|-------------------|
| `server_name`            | `"color-node"`    | `"AE-ROBOT-55"`   |
| `session_timeout`        | 60s               | 60s               |
| `reclaim_session_timeout`| 60s               | —                 |
| `debug_level`            | 1                 | 1                 |
| `admin_https`            | false             | false             |
| `no_media_timer`         | 30s               | 30s               |
| `dtls_mtu`               | 1200              | 1200              |
| `slowlink_threshold`     | 50                | 50                |
| `rtp_port_range`         | 5002–5120         | 5002–5120         |
| `admin_secret`           | `<redacted>`      | `<redacted>`      |

### WebSocket Configuration

| Parameter   | Value         |
|-------------|---------------|
| `ws_path`   | `/janus-ws`   |
| `pingpong`  | true          |
| `timeout`   | 30s           |

---

## 5. NAT Traversal

### coturn Server (82.165.177.194)

Configuration: `turnserver.conf`

| Parameter             | Value                                   |
|-----------------------|------------------------------------------|
| Listening ports       | 3478 (UDP/TCP), 443 (TLS), 5349 (alt TLS) |
| Relay port range      | 49152–65535                              |
| Realm                 | `techvisioncloud.pl`                     |
| Auth method           | Ephemeral HMAC-SHA1 (REST API)           |
| Shared secret         | `<redacted>`                             |
| Nonce lifetime        | 600s                                     |
| TLS minimum           | TLSv1.2                                 |
| User quota            | 50                                       |
| Bandwidth capacity    | 10 Mbps per TURN allocation (`bps-capacity=10000000`) |
| Loopback peers        | Disabled                                 |
| Multicast peers       | Disabled                                 |
| Private range peers   | Blacklisted (RFC 1918, 127.0.0.0/8)     |

### Ephemeral TURN Credentials (RFC 7635 / coturn REST)

Generated by `generate_turn_credentials()` in `janus.py`:

```
username = f"{int(time.time()) + ttl}:{user}"    # e.g. "1709639456:webrtc"
credential = Base64(HMAC-SHA1(username, shared_secret))
TTL = 86400s (24 hours)
```

### ICE Transport Policy

| Node   | Policy    | Reason                              |
|--------|-----------|--------------------------------------|
| Color  | `"relay"` (production env; code default=`"all"`) | Prefer TURN relay for reliable NAT traversal |
| Depth  | `"relay"` (hardcoded)              | Behind double NAT (depth→color→internet) |

### Client RTC Config (`/client-config` response)

```json
{
  "iceServers": [
    { "urls": ["stun:82.165.177.194:3478"] },
    {
      "urls": [
        "turn:82.165.177.194:3478?transport=tcp",
        "turns:82.165.177.194:443?transport=tcp"
      ],
      "username": "<ephemeral>",
      "credential": "<ephemeral>"
    }
  ],
  "iceTransportPolicy": "relay",
  "sdpSemantics": "unified-plan",
  "bundlePolicy": "balanced",
  "rtcpMuxPolicy": "require"
}
```

### NAT 1:1 Mapping Auto-Update

Script: `update-nat-mapping.sh` — Cron: `*/15 * * * *`

1. Query public IP (fallback chain): `ifconfig.me` → `api.ipify.org` → `checkip.amazonaws.com`
2. Compare against cache `/var/tmp/janus-public-ip.cache`
3. If changed → `sed` update `nat_1_1_mapping` in `/opt/janus/etc/janus/janus.jcfg`
4. Restart `janus.service`

Depth node NAT: queries color node's `/janus/nat` endpoint; falls back to local `/etc/robot/janus-nat.json`, then baked-in defaults.

> **Atomic writes (P0-2):** Both `janus-nat.json` and `janus.jcfg` are written atomically via `tempfile.mkstemp()` + `os.fsync()` + `os.rename()`. Prevents corruption on power loss during write.

### NAT Configuration (janus.jcfg)

| Parameter           | Value                    |
|---------------------|--------------------------|
| `stun_server`       | 82.165.177.194           |
| `stun_port`         | 3478                     |
| `turn_server`       | 82.165.177.194           |
| `turn_port`         | 3478                     |
| `turn_type`         | tcp                      |
| `turn_user`         | `webrtc`                 |
| `turn_pwd`          | `<redacted>`             |
| `nat_1_1_mapping`   | 87.156.23.54 (dynamic, **color only**) |
| `ice_tcp`           | false                    |
| `full_trickle`      | true                     |
| `keep_private_host` | true                     |
| `ignore_mdns`       | true                     |
| `ice_ignore_list`   | docker, veth, lo, vmnet, tailscale |
| `min_port`          | 40000                    |
| `max_port`          | 41000                    |

---

## 6. Firewall & QoS

### iptables Rules

Default policy: `INPUT DROP`, `OUTPUT ACCEPT`, `FORWARD ACCEPT` (color only, for routing).

**Color Node** (`firewall-color.sh`):

| Chain  | Proto | Port(s)       | Source              | Purpose              |
|--------|-------|---------------|---------------------|----------------------|
| INPUT  | —     | —             | lo                  | Loopback             |
| INPUT  | —     | —             | ESTABLISHED,RELATED | Conntrack            |
| INPUT  | ICMP  | —             | Any                 | Ping                 |
| INPUT  | TCP   | 22            | Any                 | SSH                  |
| INPUT  | TCP   | 8900          | 127.0.0.0/8, 192.168.1.0/24 | FastAPI (LAN)    |
| INPUT  | TCP   | 8088          | 127.0.0.0/8, 192.168.1.0/24 | Janus REST (LAN) |
| INPUT  | TCP   | 8188          | 127.0.0.0/8, 192.168.1.0/24 | Janus WS (LAN)   |
| INPUT  | TCP   | 7088          | 192.168.1.0/24      | Janus Admin (LAN)    |
| INPUT  | UDP   | 40000:41000   | Any                 | ICE candidates       |
| INPUT  | UDP   | 5002:5120     | 127.0.0.1, 192.168.1.0/24 | RTP ingest    |
| INPUT  | TCP   | 9000          | 127.0.0.0/8, 192.168.1.0/24 | TextRoom relay (LAN) |
| INPUT  | UDP   | 68            | Any                 | DHCP client          |
| INPUT  | —     | —             | 192.168.1.55        | Depth node full      |
| INPUT  | —     | —             | (default)           | DROP + LOG `FW-DROP:` |

**Depth Node** (`firewall-depth.sh`):

| Chain  | Proto | Port(s)       | Source              | Purpose              |
|--------|-------|---------------|---------------------|----------------------|
| INPUT  | —     | —             | lo                  | Loopback             |
| INPUT  | —     | —             | ESTABLISHED,RELATED | Conntrack            |
| INPUT  | ICMP  | —             | Any                 | Ping                 |
| INPUT  | TCP   | 22            | Any                 | SSH                  |
| INPUT  | TCP   | 8088          | 127.0.0.0/8, 192.168.1.0/24 | Janus REST (LAN) |
| INPUT  | TCP   | 8188          | 127.0.0.0/8, 192.168.1.0/24 | Janus WS (LAN)   |
| INPUT  | TCP   | 7088          | 192.168.1.0/24      | Janus Admin (LAN)    |
| INPUT  | UDP   | 40000:41000   | Any                 | ICE candidates       |
| INPUT  | UDP   | 5002:5120     | 127.0.0.1           | RTP ingest (local)   |
| INPUT  | TCP   | 8900          | 192.168.1.0/24      | FastAPI (LAN only)   |
| INPUT  | TCP   | 8000          | 192.168.1.0/24      | RealSense API (LAN)  |
| INPUT  | —     | —             | tailscale0          | VPN OOB access       |
| INPUT  | —     | —             | 192.168.1.10        | Color node full      |
| INPUT  | UDP   | 68            | Any                 | DHCP client          |
| INPUT  | —     | —             | (default)           | DROP + LOG `FW-DROP:` |

### QoS — HTB + fq\_codel

**Color Node** (`qos-media.sh`):

```
         ┌─────────────── HTB Root ───────────────┐
         │                                         │
   br0 (100 Mbit)                          wlan0 (20 Mbit)
         │                                         │
  ┌──────┼──────┐                           ┌──────┼──────┐
  │      │      │                           │      │      │
1:10   1:20   1:30                        1:10   1:20   1:30
RTP   Signal  Best                        RTP   Signal  Best
70%    20%    10%                          70%    20%    10%
```

**Depth Node** (`qos-media.sh`):

```
              wlan0 (50 Mbit)
                    │
             ┌──────┼──────┐
             │      │      │
           1:10   1:20   1:30
           RTP   Signal  Best
           70%    20%    10%
```

**Traffic Class Definitions:**

| Class | Name        | BW Share | Priority | Match Criteria                  |
|-------|-------------|----------|----------|---------------------------------|
| 1:10  | RTP Media   | 70%      | 1 (high) | UDP 5002–5120, UDP 40000–41000  |
| 1:20  | Signaling   | 20%      | 2        | TCP 8088, 8188, 8900 (+8000 depth) |
| 1:30  | Best-effort | 10%      | 3 (low)  | Everything else                 |

**Leaf qdisc:** `fq_codel` on all classes (fair queuing + controlled delay).

**DSCP Marking** (mangle POSTROUTING):

| Traffic     | DSCP | TOS  | Match                            |
|-------------|------|------|----------------------------------|
| RTP Media   | EF (46) | 0xb8 | UDP 5002:5120, UDP 40000:41000  |
| Signaling   | AF31 (26) | 0x68 | TCP 8088, 8188, 8900 (+8000) |

**Bandwidth Caps:**

| Interface | Node  | Cap       |
|-----------|-------|-----------|
| br0       | Color | 100 Mbit  |
| wlan0     | Color | 20 Mbit   |
| wlan0     | Depth | 50 Mbit   |

---

## 7. FastAPI Server

Port **8900** on both nodes. Entry point: `janus_camera_page/main.py`.

### Settings (`@dataclass(frozen=True)`)

| Field                  | Env Var              | Default                       | Purpose                        |
|------------------------|----------------------|-------------------------------|--------------------------------|
| `camera_type`          | `CAM_TYPE`           | `"color_camera"`              | Node role                      |
| `camera_device`        | `CAM_DEVICE`         | `"/dev/cam-rgb"`              | V4L2 device path               |
| `service_name`         | `CAM_SERVICE`        | `"rtp-rgb@cam-rgb.service"`   | Systemd unit name              |
| `janus_url`            | `JANUS_URL`          | `http://127.0.0.1:8088/janus` | Janus core API                 |
| `janus_mount_id`       | `JANUS_MOUNT_ID`     | `1305`                        | Streaming mount ID             |
| `janus_timeout`        | `JANUS_TIMEOUT`      | `3.0`                         | HTTP timeout (seconds)         |
| `depth_cam_url`        | `DEPTH_CAM_URL`      | `http://192.168.1.55:8900`    | Depth node URL (from color)    |
| `snapshot_path`        | `SNAPSHOT_PATH`      | `"/run/cam-rgb/snapshot.jpg"` | JPEG snapshot location         |
| `turn_host`            | `TURN_HOST`          | `"82.165.177.194"`            | TURN/STUN server               |
| `turn_port`            | `TURN_PORT`          | `3478`                        | TURN port                      |
| `turn_user`            | `TURN_USER`          | `"webrtc"`                    | TURN username                  |
| `turn_pass`            | `TURN_PASS`          | `""`                          | TURN static password           |
| `turn_shared_secret`   | `TURN_SHARED_SECRET` | `""`                          | Ephemeral HMAC secret          |
| `turn_cred_ttl`        | `TURN_CRED_TTL`      | `86400`                       | Credential TTL (24h)           |
| `ice_policy`           | `ICE_POLICY`         | `"all"`                       | ICE transport policy           |
| `watchdog_enabled`     | `WATCHDOG_ENABLED`   | `True`                        | Enable stream watchdog         |
| `watchdog_interval_sec`| `WATCHDOG_INTERVAL`  | `8`                           | Watchdog poll interval         |
| `watchdog_stale_ms`    | `WATCHDOG_STALE_MS`  | `10000`                       | Stream age threshold           |
| `watchdog_grace_sec`   | `WATCHDOG_GRACE`     | `60`                          | Post-startup grace period      |
| `max_fdir_reboots`     | `MAX_FDIR_REBOOTS`   | `2`                           | Circuit breaker limit          |

> **Startup validation (P0-10):** `__post_init__()` logs WARNING if neither `TURN_PASS` nor `TURN_SHARED_SECRET` is set — credentials are required for relay-only ICE policy to work.

### API Routes

**Janus proxy & NAT** (`routes/janus.py`):

| Endpoint            | Method | Auth  | Purpose                              |
|---------------------|--------|-------|--------------------------------------|
| `/janus/healthz`    | GET    | —     | Mount health check                   |
| `/janus`            | ALL    | —     | HTTP proxy to Janus REST API         |
| `/janus-ws`         | WS     | —     | WebSocket proxy to Janus             |
| `/client-config`    | GET    | —     | ICE servers + transport policy       |
| `/janus/nat`        | GET    | Admin | Current NAT configuration            |
| `/janus/nat`        | POST   | Admin | Update NAT → atomic rewrite janus.jcfg → restart |
| `/janus/nat/refresh`| POST   | Admin | Force public IP re-detection         |
| `/janus/restart`    | POST   | Admin | Restart janus.service                |

> **Async proxy safety (v1.2):** `janus_proxy.py` and `depth_camera_proxy.py` guard global `httpx.AsyncClient` lifecycle with `asyncio.Lock` — prevents double-close / use-after-close during concurrent requests.

> **Relay proxy lock (v1.3):** `relay_proxy.py` now also guards `_client` with `asyncio.Lock` — prevents concurrent `relay_get()` calls from creating duplicate `httpx.AsyncClient` instances.

> **Janus response validation (v1.2):** `janus.py` correctly traverses double-nested `data.info.info` structure in Janus streaming plugin responses. Provides safe defaults (`audioPt=0`, `videoCodec="h264"`, `videoFmtp=""`) when keys are missing.

> **Janus handle timeout (v1.3):** `with_streaming_handle` decorator wraps the inner function with `concurrent.futures.ThreadPoolExecutor` + 30s timeout. Prevents permanent session+handle leaks if wrapped function hangs. `janus_detach()`/`janus_destroy()` now return `bool`; caller logs `ERROR` on cleanup failure for observability.

> **janus_summary guard (v1.3):** Top-level `try/except` returns empty dict on any exception, insuring against future Janus API structural changes.

**Camera config** (`routes/camera.py`):

| Endpoint            | Method | Auth  | Purpose                              |
|---------------------|--------|-------|--------------------------------------|
| `/modes`            | GET    | —     | V4L2 modes (v4l2-ctl --list-formats-ext) |
| `/config`           | GET    | Admin | Current cam-rgb.env as JSON          |
| `/config`           | POST   | Admin | Write env → restart rtp-rgb@         |
| `/snapshot.jpg`     | GET    | —     | Latest JPEG (no-cache)               |
| `/controls`         | GET    | —     | V4L2 control registers               |
| `/controls`         | POST   | Admin | Apply V4L2 controls                  |

**Depth proxy** (`routes/depth_proxy.py`) — Color node only, reverse-proxies to 192.168.1.55:8900:

| Endpoint                              | Method | Purpose                          |
|---------------------------------------|--------|----------------------------------|
| `/api/v1/depth_camera/janus`          | GET/POST | Depth Janus REST proxy         |
| `/api/v1/depth_camera/janus-ws`       | WS     | Depth Janus WebSocket proxy      |
| `/api/v1/depth_camera/client-config`  | GET    | Depth ICE config                 |
| `/api/v1/depth_camera/snapshot.jpg`   | GET    | Depth snapshot                   |
| `/api/v1/depth_camera/depth`          | GET    | Depth value at (x,y)            |
| `/api/v1/depth_camera/depth/frame`    | GET    | Full depth map                   |
| `/api/v1/depth_camera/depth/frame_color_overlay` | GET | Depth + color aligned  |
| `/api/v1/depth_camera/{path:path}`    | GET    | Static file proxy                |

**System** (`routes/system.py`):

| Endpoint            | Method | Purpose                              |
|---------------------|--------|--------------------------------------|
| `/healthz`          | GET    | Deep health (Janus + stream + mode)  |
| `/health/stream`     | GET    | End-to-end stream health (media-level) |
| `/status`           | GET    | Full system status snapshot          |
| `/action/restart`   | POST   | Restart camera service               |
| `/janus.js`         | GET    | Janus JS library (local or CDN)      |
| `/relay/time`       | GET    | Server time (clock sync)             |
| `/relay/pong`       | GET    | Latest joystick ping/pong result     |

**FDIR** (`routes/fdir.py`):

| Endpoint              | Method | Purpose                           |
|------------------------|--------|-----------------------------------|
| `/fdir/ladder`         | GET    | Recovery ladder status            |
| `/fdir/events`         | GET    | Recent FDIR events (newest first) |
| `/fdir/mode`           | GET    | Current system mode               |
| `/fdir/mode/{target}`  | POST   | Force mode transition             |
| `/fdir/ladder/reset`   | POST   | Reset ladder to level 0           |

**Telemetry & Metrics:**

| Endpoint       | Method | Purpose                              |
|----------------|--------|--------------------------------------|
| `/telemetry`   | POST   | Ingest WebRTC client stats (204 No Content) |
| `/metrics`     | GET    | Prometheus text exposition           |

### Security

**Admin authentication:**

| Variable           | Default       | Purpose                  |
|--------------------|---------------|--------------------------|
| `CAM_ADMIN_TOKEN`  | `"change-me"` | Admin token (header)    |
| `CAM_ADMIN_ENFORCE`| `1`           | 1=reject (503 if default token), 0=warn  |
| `CAMCTRL_API_KEY`  | —             | Optional X-Api-Key header|

**Security headers** (middleware):

| Header                    | Value                                      |
|---------------------------|---------------------------------------------|
| X-Content-Type-Options    | nosniff                                     |
| X-Frame-Options           | _(removed — superseded by CSP frame-ancestors)_ |
| Content-Security-Policy   | `frame-ancestors 'self' https://*.techvisioncloud.pl http://192.168.1.10:8900 http://192.168.1.55:8900 https://blupassionsystem.de:8443` + script/style/connect/img/media directives |
| Referrer-Policy           | strict-origin-when-cross-origin             |
| Permissions-Policy        | camera=(), microphone=()                    |

**CORS origins** (`allow_origin_regex`):
```
^https?://(localhost|127\.0\.0\.1|192\.168\.1\.\d{1,3})(:\d+)?$
|^https://[\w-]+\.techvisioncloud\.pl$
```
Override: `CORS_ORIGIN_REGEX` env var.  Starlette applies `re.fullmatch()`.

### TextRoom Relay (port 9000)

Service: `janus_camera_page_hook.service` — `textroom_relay.py`

| Endpoint          | Method | Purpose                              |
|-------------------|--------|--------------------------------------|
| `/textroom-hook`  | POST   | Janus webhook (queue joystick frame) |
| `/health`         | GET    | Status + counters                    |
| `/time`           | GET    | Server time (clock sync)             |
| `/pong`           | GET    | Latest ping→robot round-trip         |

**Forward worker:**
- Target: `http://127.0.0.1:8110/joystick/frame`
- Queue: max 50 frames (FIFO, drop oldest on overflow)
- Dedup: skip if `ts ≤ last_forwarded`
- Persistent `httpx.AsyncClient` (keep-alive pooling)

**Joystick frame:**
```json
{
  "type": "joystick",
  "ts": 1709639456789,
  "axes": [-0.5, 0.0, 0.3, 0.0],
  "buttons": [false, true, false, ...],
  "relay_rx_ms": 1709639456800
}
```

---

## 8. Depth API & Contract

Cross-ref: [DEPTH_SEMANTIC_CONTRACT.md](../janus_camera_page/DEPTH_SEMANTIC_CONTRACT.md)

### Endpoints (port 8000, depth node only)

| Endpoint                   | Method | Format     | Response                                   |
|----------------------------|--------|------------|---------------------------------------------|
| `/depth?x=<0-100>&y=<0-100>` | GET | JSON     | `{"type": "depth", "x": 50, "y": 50, "depth": 1.234}` |
| `/color_frame?format=json` | GET    | JSON       | `{"data": "<base64 RGB24>", "width": 480, "height": 640}` |
| `/color_frame?format=raw`  | GET    | Binary     | Raw RGB24 bytes                             |
| `/depth_map?format=json`   | GET    | JSON       | `{"data": "<base64 float32>", "width": 480, "height": 640}` |
| `/depth_map?format=raw`    | GET    | Binary     | Raw float32 array (row-major)               |
| `/depth/frame_color_overlay?format=json` | GET | JSON | Combined depth+color aligned frame |

### Depth frame\_color\_overlay Response

```json
{
  "width": 480,
  "height": 640,
  "timestamp": 1742000000.0,
  "rgb_data": "<base64 Uint8Array RGB24>",
  "rgb_dtype": "uint8-rgb24",
  "depth_data": "<base64 float32>",
  "depth_dtype": "float32"
}
```

> **Alias support:** The 3D viewer (`depth-api.ts`) accepts both schemas — it
> probes for `depth_data|depth|data` and `mapped_color|rgb_data|color` fields,
> so both the old `mapped_color` key and the current `rgb_data` key work.

### Camera Intrinsics

| Parameter | Value              | Unit   |
|-----------|--------------------|--------|
| fx        | 380.4253845214844  | pixels |
| fy        | 380.4253845214844  | pixels |
| cx        | 232.37411499023438 | pixels |
| cy        | 324.824951171875   | pixels |
| width     | 480                | pixels |
| height    | 640                | pixels |

### Depth Calibration

| Parameter | Value  | Unit |
|-----------|--------|------|
| k (scale) | 0.957 | —    |
| b (offset)| 45.2  | mm   |

Formula: `true_mm = k × (raw × depthScale) + b = 0.957 × (raw × 0.9) + 45.2`

### Semantic Contract — H.264 Stream vs HTTP API

| Aspect       | H.264 WebRTC Stream            | HTTP Raw API                    |
|--------------|--------------------------------|---------------------------------|
| Data         | Pseudo-color (jet palette)     | Metric float32 (meters)         |
| Precision    | Lossy (H.264 quantization)     | Lossless (raw values)           |
| Invalid      | Black (0,0,0)                  | 0.0                             |
| Use case     | Visual feedback                | Measurement, 3D reconstruction  |
| Transport    | DTLS-SRTP (WebRTC)             | HTTP GET (polling)              |

### Breaking Changes (require full regression)

- Resolution or FPS change
- Colorizer palette settings
- Rotation direction/angle
- Depth scale source/formula
- `rs.align()` addition/removal
- H264 encoder settings (profile, bitrate, GOP)
- Raw depth API response format
- Camera intrinsic values

---

## 9. Browser Player

### Architecture — Hexagonal (Ports & Adapters)

```
┌─────────────────────────────────────────────┐
│                  BOOTSTRAP                   │
│  (6-step init, DOMContentLoaded entry)       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│                    APP                        │
│  PlayerController  ReconnectCoordinator       │
│  WatchdogService   StatsService               │
│  JoystickService   TimerCoordinator           │
│  RecoveryMap                                  │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│                   PORTS                       │
│  StreamingPort  VideoPort  LoggerPort         │
│  ClockPort                                    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│                 ADAPTERS                      │
│  JanusSessionManager   JanusStreamingAdapter  │
│  JanusTextRoomAdapter  DomUIAdapter           │
│  createClock()         createConsoleLogger()  │
│  Telemetry (legacy)                           │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│                   CORE                        │
│  StateMachineCanonical  ConnectionPolicy      │
│  RecoveryPolicy         DomainEvents          │
│  PlayerState            BackoffCalc           │
│  Invariants (L4/L5/L6)  FailClosed           │
│  Codes                                       │
└──────────────────────────────────────────────┘
```

### File Manifest (33 application files)

**Core (10):** `backoff.js`, `codes.js`, `connection_policy.js`, `domain_events.js`, `fail_closed.js`, `invariants.js`, `player_state.js`, `recovery_policy.js`, `state_machine_canonical.js`, `state_machine_legacy.js`

**Adapters (7):** `clock.js`, `dom_ui_adapter.js`, `janus_session_manager.js`, `janus_streaming_adapter.js`, `janus_textroom_adapter.js`, `logger.js`, `telemetry.js`

**Ports (4):** `clock_port.js`, `logger_port.js`, `streaming_port.js`, `video_port.js`

**App (7):** `joystick_service.js`, `player_controller.js`, `reconnect_coordinator.js`, `recovery_map.js`, `stats_service.js`, `timer_coordinator.js`, `watchdog_service.js`

**Meta (3):** `bootstrap.js`, `config.js`, `ns.js`

**Tests (6):** `run_core_tests.js`, `run_coordinator_tests.js`, `run_config_tests.js`, `run_controller_tests.js`, `run_app_tests.js`, `run_adapter_tests.js`

### State Machine (StateMachineCanonical)

**States:** `IDLE`, `CONNECTING`, `PLAYING`, `RECONNECTING`, `ERROR`

**Transition table:**

```
IDLE
  ├─ PLAY_REQUEST ──────────────────────► CONNECTING
  
CONNECTING
  ├─ STREAM_RECOVERED ──────────────────► PLAYING  (preserves snap.firstFrameReceived)
  ├─ RECONNECT_SCHEDULED / ICE_FAILED ──► RECONNECTING
  ├─ CONNECT_FAILED ────────────────────► ERROR
  └─ STOP_REQUEST ──────────────────────► IDLE

PLAYING
  ├─ WEBRTC_DOWN / RECONNECT_SCHEDULED ─► RECONNECTING
  ├─ ICE_FAILED ────────────────────────► RECONNECTING
  ├─ STOP_REQUEST ──────────────────────► IDLE
  └─ PLAY_REQUEST / STREAM_RECOVERED ──► PLAYING (no-op)

RECONNECTING
  ├─ STREAM_RECOVERED / RECONNECT_SUCCESS ► PLAYING
  ├─ RECONNECT_EXHAUSTED ──────────────► ERROR
  ├─ RECONNECT_SCHEDULED ──────────────► RECONNECTING (no-op)
  └─ STOP_REQUEST ──────────────────────► IDLE

ERROR
  └─ RESET / PLAY_REQUEST ─────────────► CONNECTING

All states: POLICY_MARK_DEGRADED, FIRST_FRAME_RECEIVED,
            STREAMING_OFFER_RECEIVED (data-only transitions)
```

**Definition of Connected:**
```
connected := webrtcUp === true AND firstFrameReceived === true
```

### Invariants

| ID | Rule                                        | Fail action |
|----|---------------------------------------------|-------------|
| L4 | PLAYING ⇒ webrtcUp AND firstFrameReceived   | ERROR       |
| L5 | RECONNECTING ⇒ attempts ≥ 1                | ERROR       |
| L6 | IDLE or ERROR ⇒ NOT webrtcUp AND NOT firstFrameReceived | ERROR |

Checked by `InvariantGate.check(snapshot)` — throws `InvariantViolation` on failure (fail-closed, L17).

### Connection Policy

`ConnectionPolicy.decide(eventType, snapshot)` routes domain events to actions:

| Domain Event                    | Severity | Action               |
|---------------------------------|----------|----------------------|
| ICE_FAILED                      | HARD     | REQUEST_RECOVERY     |
| SESSION_RESET                   | HARD     | REQUEST_RECOVERY     |
| ALREADY_WATCHING                | HARD     | REQUEST_RECOVERY     |
| TAB_RESUME_STALE                | HARD     | REQUEST_RECOVERY     |
| NETWORK_RESTORED                | HARD     | REQUEST_RECOVERY     |
| WEBRTC_DOWN                     | MEDIUM   | REQUEST_RECOVERY     |
| HANGUP                          | MEDIUM   | REQUEST_RECOVERY     |
| MEDIA_SILENCE_TIMEOUT           | MEDIUM   | REQUEST_RECOVERY     |
| TRACK_MUTE                      | MEDIUM   | REQUEST_RECOVERY     |
| JANUS_ERROR                     | MEDIUM   | REQUEST_RECOVERY     |
| FPS_DROP                        | MEDIUM   | REQUEST_RECOVERY     |
| VIDEO_STALLED                   | MEDIUM   | REQUEST_RECOVERY     |
| ICE_DISCONNECTED_GRACE_TIMEOUT  | —        | MARK_DEGRADED        |

### Recovery Escalation Ladder

`RecoveryPolicy.decideRecoveryAction(attempt, severity, cfg)`:

```
Severity HARD → RECREATE_SESSION immediately (any attempt)

Severity MEDIUM/SOFT:
  Attempt 1..maxWatchRetries (3)     → SOFT_RESTART
  Attempt 4..4+maxReattachRetries (2) → REATTACH_PLUGIN
  Attempt 6+                          → RECREATE_SESSION
```

| RecoveryAction      | Value | Implementation                        |
|---------------------|-------|---------------------------------------|
| SOFT_RESTART        | 1     | Stop watch → re-watch same handle     |
| REATTACH_PLUGIN     | 2     | Detach + re-attach streaming plugin   |
| RECREATE_SESSION    | 3     | Destroy session → full reconnect      |

### Bootstrap Sequence (6 steps)

```
1. Config Load
   computeConfig() → body.dataset + URLSearchParams → merge + validate

2. Janus Lib Init
   ensureJanusInit() → Janus.init() async (timeout: implicit)

3. RTC Config Load
   loadRtcConfig() → GET /client-config → parse iceServers
   Fallback: Google STUN (stun:stun.l.google.com:19302)

4. Adapter Creation
   JanusSessionManager → JanusStreamingAdapter → JanusTextRoomAdapter → DomUIAdapter

5. Service Creation
   WatchdogService → StatsService → JoystickService → TimerCoordinator → ReconnectCoordinator

6. Controller Init
   PlayerController.init() → wire events → frame clock → visibility handler → autoplay test
   Result: window.autonomousPlayerController
```

### Fail-Closed Behaviors

| Situation                      | Action                               |
|--------------------------------|--------------------------------------|
| Unknown state/transition       | ERROR + LOG + CANCEL\_ALL\_TIMERS    |
| Invariant violation (L4/L5/L6) | ERROR + LOG + CANCEL\_ALL\_TIMERS    |
| Reconnect exhausted (≥ max)    | ERROR + onExhausted callback         |
| Stale event (old token/gen)    | IGNORE + log EVENT\_DROPPED          |
| Duplicate reconnect request    | IGNORE + log EVENT\_DROPPED          |
| Autoplay blocked               | ERROR + AUTOPLAY\_BLOCKED + retry btn|
| Janus init timeout (8s)        | Reject promise, bootstrap fails      |
| Session destroy timeout (5s)   | Zombie cleanup, proceed              |
| Stale Janus session callback   | IGNORE (generation guard P0-11)      |
| h.send('start') rejected       | ERROR event via error callback (P0-6)|
| Enqueue error (adapter)        | Logged + swallowed (v1.2 — was silent)|
| Concurrent init/destroy        | Serialized via `_lifecycleMutex` (v1.2)|

> **Lifecycle mutex (v1.2):** `JanusSessionManager` serializes `init()` / `destroy()` through `_lifecycleMutex = Promise.resolve()` chain. Prevents interleaved init+destroy producing zombie Janus sessions.

> **Adapter enqueue safety (v1.2):** `JanusStreamingAdapter._enqueue()` now catches and logs errors. `recreate()` rejects any pending `_pendingWatch` promise to avoid dangling await.

> **Session event cleanup (v1.3):** `JanusStreamingAdapter.stop()` and `JanusTextRoomAdapter.detach()` now unsubscribe from `session.onEvent()` callbacks — prevents listener accumulation across `RECREATE_SESSION` cycles. `DomUIAdapter` gains `destroy()` method removing all DOM event listeners.

---

## 10. Watchdog & Reconnection

### WatchdogService

**Frame arrival monitor** — dual watchdog:

| Watchdog       | Trigger                      | Default Threshold  | Action                          |
|----------------|------------------------------|--------------------|---------------------------------|
| No-frame       | `lastFrameAge > threshold`   | 3000 ms            | `onTimeout(ageMs)` → MEDIUM recovery |
| Low-FPS        | `currentFps < min` sustained | 5 FPS for 3000 ms  | `onFpsDrop()` → MEDIUM recovery (latched) |

**FPS ring buffer:** 60-entry circular buffer (sliding window).

```
getCurrentFps() = (frameCount / timespan) × 1000
```

where `timespan = ring[head].ts - ring[tail].ts` spans `count - 1` intervals (v1.2 fix — was using `count`, inflating FPS by ~1.7%).

**Watchdog tick interval:** 2000 ms (configurable via `watchdogTickMs`).

**Tab resume:** `resetAfterTabResume()` clears ring + resets origin (prevents false FPS spikes after browser tab hidden).

> **Idempotent start (P1-10):** `start()` clears any stale interval before creating a new one. Prevents timer leak if controller crashes and restarts without calling `stop()`.

### ReconnectCoordinator

**Sole owner of reconnect scheduling** (invariant L11: max one in-flight attempt).

**Backoff calculation:**
```
delay = base × factor^(attempt - 1)
      = 300 × 1.8^(attempt - 1)    [defaults]

clamped to [minMs, maxMs] = [150, 15000]
```

| Attempt | Delay (ms) | Cumulative (ms) |
|---------|------------|-----------------|
| 1       | 300        | 300             |
| 2       | 540        | 840             |
| 3       | 972        | 1812            |
| 4       | 1749       | 3561            |
| 5       | 3149       | 6710            |
| 6       | 5668       | 12378           |
| 7       | 10202      | 22580           |
| 8+      | 15000      | — (capped)      |

**Jitter:** deterministic (seedable xorshift32, not Math.random) — ±`jitterRatio` of base. Default `jitterRatio=0.0` (disabled).

**Settle window:** After successful reconnect, wait `connectSettleMs` (6000 ms) for first frame before declaring recovered. If no frame within `settleStartTimeoutMs` (15000 ms), escalate.

> **Timer safety (P0-7):** On attempt failure (catch block), `_settleStartTimeoutTimer` is cleared to prevent a stale timer from scheduling a duplicate second attempt.

> **Settle timer token guard (v1.2):** `_settleStartTimeout` callback now verifies `token !== this._ctx.getToken()` and `shouldContinue()` before calling `_scheduleNext()`. Prevents a stale settle timeout from spuriously escalating after token rotation.

**Exhaustion:** `maxReconnectAttempts` = 12 → ERROR state.

**Tab visibility handling:**

| Transition       | Hidden duration > sessionTimeout (60 s) | Hidden duration ≤ sessionTimeout |
|------------------|----------------------------------------|----------------------------------|
| Tab visible (ERROR) | retry() → fresh CONNECTING          | retry() → fresh CONNECTING       |
| Tab visible (RECONNECTING) | Escalate to HARD (RECREATE)  | resumeIfPending()               |
| Tab visible (PLAYING) | requestRecovery(HARD)             | resetAfterTabResume()           |

### JoystickService

**Gamepad polling:** 30 ms interval via `navigator.getGamepads()`.

**Transports:**
1. HTTP POST `/joystick/frame` (primary)
2. DataChannel via JanusTextRoomAdapter (secondary, room ID=1000)

**E2E latency measurement:**

```
E2E = RTT - HTTP_return - pong_age + robot_latency

Clock sync (NTP-style):
  offset = server_ms - (t1 + t2) / 2
  RTT-filtered, EMA smoothing (α=0.4 first 5, 0.15 after)
  60s backoff between sync attempts

Ping-pong: 2s interval, polled from /relay/pong
Spike rejector: poll_rtt > 4 × EMA or > 6 × best_poll_rtt → discard
```

### Player Configuration Defaults

| Parameter                 | Config Key               | Default   |
|---------------------------|--------------------------|-----------|
| Frame timeout             | `noFrameThresholdMs`     | 3000 ms   |
| Watchdog tick              | `watchdogTickMs`         | 2000 ms   |
| Track mute timeout        | `trackMuteRestartMs`     | 3000 ms   |
| ICE disconnected grace    | `iceDisconnectedGraceMs` | 3000 ms   |
| Settle window             | `connectSettleMs`        | 6000 ms   |
| Settle start timeout      | `settleStartTimeoutMs`   | 15000 ms  |
| Backoff base              | `backoffBaseMs`          | 300 ms    |
| Backoff min               | `backoffMinMs`           | 150 ms    |
| Backoff max               | `backoffMaxMs`           | 15000 ms  |
| Backoff factor            | `backoffFactor`          | 1.8       |
| Backoff jitter ratio      | `backoffJitterRatio`     | 0.0       |
| Min acceptable FPS        | `minAcceptableFps`       | 5         |
| FPS drop threshold        | `fpsDropThresholdMs`     | 3000 ms   |
| Max reconnect attempts    | `maxReconnectAttempts`   | 12        |
| Max watch retries (SOFT)  | `maxWatchRetries`        | 3         |
| Max reattach retries      | `maxReattachRetries`     | 2         |
| Session timeout           | `sessionTimeoutMs`       | 60000 ms  |
| Error auto-retry base     | `errorAutoRetryBaseMs`   | 10000 ms  |
| Error auto-retry max      | `errorAutoRetryMaxMs`    | 120000 ms |
| Visibility-aware reconnect| `visibilityAwareReconnect`| true     |

### Timeout Contract (cross-layer source of truth)

All timeout values must satisfy the invariants below.  When changing one value,
check the chain — a mismatch causes premature recovery or orphan sessions.

| Layer | Parameter | Value | Source file | Constraint |
|-------|-----------|-------|-------------|------------|
| **Janus** | `session_timeout` | 60 s | `janus.jcfg` | = `sessionTimeoutMs / 1000` (currently 60 000 / 1000 = 60) |
| **Janus** | `reclaim_session_timeout` | 60 s | `janus.jcfg` | = `session_timeout` (allow full reclaim window) |
| **Janus** | `no_media_timer` | 30 s | `janus.jcfg` | > player `noFrameThresholdMs / 1000` (avoid Janus killing session before player recovers) |
| **Player** | `sessionTimeoutMs` | 60 000 ms | `config.js` / `data-session-timeout-ms` | = Janus `session_timeout × 1000` |
| **Player** | `noFrameThresholdMs` | 3 000 ms | `config.js` | < Janus `no_media_timer × 1000` |
| **Player** | `connectSettleMs` | 6 000 ms | `config.js` | ≥ typical ICE + DTLS + first-frame latency |
| **Player** | `settleStartTimeoutMs` | 15 000 ms | `config.js` | ≥ TURN allocation + longest ICE path |
| **FastAPI** | `watchdog_interval_sec` | 8 s | `settings.py` / `CAM_WATCHDOG_INTERVAL` | Independent of player; checks server-side mount freshness |
| **FastAPI** | `watchdog_stale_ms` | 10 000 ms | `settings.py` / `CAM_WATCHDOG_STALE_MS` | > 2 × Janus streaming period (~1/fps) |
| **FastAPI** | `watchdog_grace_sec` | 60 s | `settings.py` / `WATCHDOG_GRACE_SEC` | Startup grace; ≥ typical cold-start TTFF |
| **systemd** | `WatchdogSec` | 30 s | `janus-camera-page.service` | sd_notify heartbeat at `WatchdogSec/2` (15 s) |
| **systemd** | `RestartSec` | 5 s | `override.conf` | Recovery delay between crash-restart cycles |
| **TURN** | `TURN_CRED_TTL` | 86 400 s | `settings.py` / `TURN_CRED_TTL` | >> session duration; rotated daily |
| **TURN** | `stale-nonce` | 600 s | `turnserver.conf` | Short enough to limit credential reuse |

**Key invariant:** `noFrameThresholdMs / 1000` < `no_media_timer` < `session_timeout` = `sessionTimeoutMs / 1000`.  
This ensures the player detects and recovers before Janus tears down the session.

---

## 11. 3D Viewer

TypeScript application under `frontend_service/viewer/src/`.

### Data Pipeline

```
Depth Node HTTP ──► DepthApi (poll + base64 decode)
  ──► DepthProcessor (stride-4 pinhole unprojection)
  ──► Orchestrator (transform chain + voxel accumulation)
  ──► DepthCloudVisual (Three.js BufferGeometry)
  ──► WebGL render
```

### DepthApi — HTTP Long-Poll

- Endpoint: `GET /api/v1/depth_camera/depth/frame_color_overlay?format=json`
- Fetch timeout: 8000 ms
- In-flight guard: single concurrent request
- Decodes `depth_data` (base64 → float32 array) and `rgb_data` (base64 → Uint8Array RGB24) — with alias fallback to `mapped_color`/`depth`/`data`/`color`

### DepthProcessor — Pinhole Unprojection

`processDepthFrame(depthRaw, rgbRaw, width, height, intrinsics, calibration, overlay, depthScale, timestampMs) → PointCloud`

**Pipeline (stride=4, max 19200 points from 480×640):**

1. Read raw pixel: `depthRaw[row × width + col]`
2. Validation filters:
   - Skip if `raw == 0`
   - Skip if `raw < minRaw (50)` or `raw > maxRaw (2000)`
   - Optional: near-black RGB reject (RGB ≤ 16)
3. Depth calibration: `true_mm = 0.957 × (raw × 0.9) + 45.2`
4. Distance gate: skip if `depth_m < max(0.05, config.minDistanceM)`
5. Pixel rotation (90° CW for portrait mount):
   - Pre-rotated intrinsics: `rCx = height-1-cy`, `rCy = cx`, `rFx = fy`, `rFy = fx`
   - New coords: `(u,v) → (h-1-v, u)` in 640×480 frame
6. Pinhole unprojection (camera-local meters):
   ```
   x = (u - cx) / fx × depth
   y = (v - cy) / fy × depth
   z = depth
   ```
7. Mirror flips: `flipX`, `flipY`, `cloudMirrorVertical` (if configured)

**Output:** `PointCloud { positions: Float32Array[count×3], colors: Float32Array[count×3], count, timestampMs }`

### DepthCloudVisual — Three.js Renderer

```
MAX_LIVE_POINTS = ceil(640/4) × ceil(480/4) = 160 × 120 = 19200
```

**Pre-allocated buffers (zero per-frame GC):**
- `position`: `Float32Array[19200 × 3]`
- `color`: `Float32Array[19200 × 3]`
- `setDrawRange(0, count)` — only render valid points

**Material:**
```
THREE.PointsMaterial {
  sizeAttenuation: false    // pixel-space sizing (constant on screen)
  vertexColors: true
  depthTest: true
  depthWrite: true
  depthFunc: LessEqualDepth
}
```

**Voxel map (accumulated point cloud):**
- Separate BufferGeometry: pre-allocated 400k × 3
- Opacity: 0.9
- Persistence: DMP1 binary format via `DepthApi.saveVoxelMap()` / `loadVoxelMap()`

### TransformAuthority — Kinematic Chain

```
world
  └── agv           (RosPose from Symovo API, ~500ms)
       └── mount    (tilt→pitch, rotation→roll from xArm, ~100ms)
            └── armBase  (static CAD offset)
                 └── lift     (motor units → displacement, Igus API, ~100ms)
                      └── flange   (FK from 6 joint angles, ~100ms)
                           ├── camera   (static calibration)
                           └── gripper  (static STL origin)
```

**Static camera transform (flangeToCamera):**

| Component    | Value                                |
|--------------|--------------------------------------|
| Translation  | x=0.03, y=-0.03, z=-0.15 (meters)   |
| Rotation     | roll=-90°, pitch=90°, yaw=0°         |

### Coordinate System Mapping

| Axis  | ROS Convention    | Three.js Convention |
|-------|-------------------|---------------------|
| X     | Forward           | Right               |
| Y     | Left              | Up                  |
| Z     | Up                | Into screen (neg)   |

**Change-of-Basis matrix (ROS→Three.js):**
```
[  0  -1   0   0 ]
[  0   0   1   0 ]
[ -1   0   0   0 ]
[  0   0   0   1 ]
```

Mapping: ROS X→Three -Z, ROS Y→Three -X, ROS Z→Three +Y.

**World units:** `SCALE_FACTOR = 20` (1 meter = 20 world units, 1 WU = 50 mm).

### Scene Configuration Constants

| Parameter                | Value                    |
|--------------------------|--------------------------|
| `depthScale`             | 0.9 (raw → mm)           |
| `frustum.near`           | 0.105 m                  |
| `frustum.far`            | 1.0 m                    |
| `fov.h`                  | 64.47°                   |
| `fov.v`                  | 80.16°                   |
| `stridePx`               | 4                        |
| `minRaw`                 | 50                       |
| `maxRaw`                 | 2000                     |
| `minDistanceM`           | 0.05 m (hard floor)      |
| `nearBlackThreshold`     | 16                       |
| `pixelRotationDeg`       | 90 (CW)                  |
| `cloudMirrorVertical`    | true                     |
| `cloudMirrorAxis`        | 'y'                      |
| `voxelSizeMm`            | 5                        |
| `maxVoxels`              | 400,000                  |

### Orchestrator

**Responsibilities:**
1. Subscribe to raw events (`robot:status`, `raw:depthFrame`)
2. Normalize robot state (joints, lift, AGV pose, mount)
3. Compute FK → per-joint rotations
4. Process depth frames → PointCloud
5. Transform to world frame via TransformAuthority
6. Accumulate voxel map (ring-buffer, max 400k)
7. Enforce timestamp skew policy

**Timestamp skew gate:** `SKEW_HARD_LIMIT_MS = 80` — if frame timestamp and pose timestamp differ by more than 80 ms, drop the frame (`depth:dropped { reason: 'skew' }`).

---

## 12. FDIR

Cross-ref: [RELIABILITY_CHECKLIST.md](../janus_camera_page/RELIABILITY_CHECKLIST.md)

### 5-Level Recovery Ladder

| Level | Name               | Action           | Max Attempts | Cooldown | Notes                              |
|-------|--------------------|------------------|--------------|----------|-------------------------------------|
| 0     | retry\_handle      | RETRY\_HANDLE    | 1            | 10s      | Verify Janus mount alive            |
| 1     | restart\_pipeline  | RESTART\_PIPELINE| 5            | 45s      | `systemctl restart rtp-rgb@cam-rgb` |
| 2     | restart\_janus     | RESTART\_JANUS   | 3            | 90s      | `systemctl restart janus.service`   |
| 3     | usb\_reset         | USB\_RESET       | 2            | 90s      | Depth only: `realsense-failsafe.service` |
| 4     | reboot\_node       | REBOOT\_NODE     | 1            | 300s     | `systemctl reboot` (circuit breaker)|

**Escalation:** budget exhaustion at level N → try level N+1. Dedup window: 3s monotonic timestamp (prevents thundering herd).

> **Thread safety (v1.2):** All `RecoveryLadder` public methods (`escalate`, `reset`, `healthy_tick`, `save_state`, `load_state`) are serialized via `threading.Lock`. Prevents concurrent watchdog + API route from corrupting ladder state.

> **Retry-before-escalation (v1.2):** `watchdogs.py` retries Janus handle verification once with 2s backoff before escalating to the recovery ladder. Eliminates single-packet-loss false positives that previously triggered unnecessary pipeline restarts.

**Recovery state persistence:**

| Path                                      | Scope                  | Content                      |
|-------------------------------------------|------------------------|------------------------------|
| `/run/camera/fdir_ladder.json`            | Process (tmpfs)        | Level, attempts, timestamps  |
| `/var/lib/camera-fdir/reboot_count`       | Persistent (survives reboot) | Integer counter        |
| `/var/lib/camera-fdir/last_reboot_request`| Persistent             | `{ts, signal}` JSON          |

> **Atomic reboot counter (v1.2):** `reboot_count` file is read/written under `fcntl.flock(LOCK_EX)` — prevents race between concurrent readers (API route) and writer (reboot action). State save uses `tempfile` + `os.fsync()` + `os.rename()` for crash-safe persistence.

> **Ladder state read lock (v1.3):** `_load_ladder_state()` now acquires `fcntl.flock(LOCK_SH)` during reads — consistent with the locked reboot counter pattern.

**Reboot circuit breaker:** if `reboot_count ≥ max_fdir_reboots` (default 2) → enter SAFE mode instead of rebooting. Prevents infinite reboot loops.

**Healthy recovery:** 10 consecutive healthy watchdog checks → `ladder.reset()` + `promote(NOMINAL)`.

### System Operating Modes

| Mode         | Level | Streams | Max FPS | Max Bitrate | TURN | Uplink | Entry condition                |
|--------------|-------|---------|---------|-------------|------|--------|--------------------------------|
| NOMINAL      | 0     | Yes     | 30      | 4000 kbps   | Yes  | Yes    | Boot / 10 healthy checks       |
| DEGRADED     | 1     | Yes     | 15      | 1500 kbps   | Yes  | Yes    | Transient fault recovery        |
| LOCAL\_ONLY  | 2     | Yes     | 15      | 2000 kbps   | No   | No     | Uplink lost                     |
| SAFE         | 3     | No      | 0       | 0 kbps      | No   | No     | Critical fault / reboot exhaustion |

**Transitions:** `degrade(reason)` drops one level; `promote(target, reason)` raises only if target.level < current.level. All transitions emit FDIR events and notify listeners.

> **Listener timeout (v1.2):** `system_mode.py` executes mode-change listener callbacks via `concurrent.futures.ThreadPoolExecutor` with a 5s timeout. A hanging listener (e.g. blocked on subprocess) cannot stall mode transitions.

**Prometheus metrics:** `system_mode` gauge (0–3), `mode_transitions_total` counter.

### Thermal De-Rate

Source: `/sys/class/thermal/thermal_zone0/temp` (BCM2835, Pi 5). Poll: 10s.

| Threshold | Env Var           | Default | Action                         |
|-----------|-------------------|---------|--------------------------------|
| WARN      | `THERMAL_WARN_C`  | 70°C    | FPS\_PROFILE=low → DEGRADED   |
| CRIT      | `THERMAL_CRIT_C`  | 80°C    | FPS\_PROFILE=stop → SAFE      |
| RESUME    | `THERMAL_RESUME_C`| 65°C    | FPS\_PROFILE=normal → promote  |

FPS profile written to `/run/camera/fps_profile`. Hysteresis prevents oscillation (resume < warn).

**Prometheus metric:** `cpu_temp_celsius` gauge.

> **Specific exception handling (v1.2):** `thermal.py` catches `PermissionError` and `FileNotFoundError` separately (instead of blanket `Exception`). Each logs a targeted message, improving diagnostics when the thermal zone sysfs is unavailable.

### FDIR Event Logging

`FdirEvent` (frozen dataclass):

| Field              | Type   | Example                        |
|--------------------|--------|--------------------------------|
| `timestamp`        | float  | Unix time                       |
| `domain`           | str    | sensor, pipeline, janus, network, turn, client, system |
| `severity`         | str    | info, warn, error, critical     |
| `detection_signal` | str    | `"video_age_ms=12000"`          |
| `recovery_action`  | str    | Enum value                      |
| `outcome`          | str    | `"restarted pipeline"`          |
| `details`          | dict   | `{attempt: 2, level: "restart_pipeline"}` |
| `node`             | str    | hostname                        |

**Sinks:**
1. In-memory ring buffer (deque, maxlen=500, env `FDIR_RING_MAX`)
2. Python logging (severity → log level)
3. Prometheus counter: `fdir_events_total[domain, severity]`
4. Append-only JSON lines: `/var/log/camera-fdir/fdir.jsonl`

> **Size-based log rotation (v1.2):** `fdir.jsonl` is rotated when it exceeds `_PERSIST_MAX_BYTES` (5 MB, env `FDIR_PERSIST_MAX_BYTES`). One backup file (`fdir.jsonl.1`) is kept. Prevents unbounded disk growth on long-running deployments.

---

## 13. Systemd Map

### Color Node (.10) — Service Dependency Chain

```
                    network-online.target
                           │
                    ┌──────┴──────┐
                    │             │
              janus.service      │
              │    │             │
              │    └─────────────┤
              │                  │
    rtp-rgb@cam-rgb.service   janus-camera-page.service
              │                  │
              │           janus_camera_page_hook.service
              │           (textroom relay, port 9000)
              │
         cam-wait-capture.sh
         (ExecStartPre)
```

**janus.service:**
- ExecStart: `/opt/janus/bin/janus -F /opt/janus/etc/janus`
- User: `janus`
- Restart: on-failure (2s)
- LimitNOFILE: 65535
- Drop-in: override.conf (v1.3 — consolidated from 4 files)
  - `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`
  - `ReadWritePaths=/opt/janus/etc/janus`, `/opt/janus/etc/janus/streams.d`, `/opt/janus/var`

**janus-watchdog.timer** (v1.4):
- Fires every 30s; curls `http://127.0.0.1:8088/janus/info`
- On failure → restarts `janus.service`
- BindsTo: `janus.service` (timer stops when Janus stops)

**janus-camera-page.service:**
- After: janus.service, network-online.target
- Type: notify (sd_notify READY=1 on startup)
- WatchdogSec: 30s (sd_notify WATCHDOG=1 heartbeat every 15s)
- ExecStart: `/usr/bin/python3 main.py`
- Restart: always (2s → 5s via override)
- Nice: -5
- StartLimitBurst: 5 per 300s
- EnvironmentFile: `/etc/robot/camera-secrets.env`
- StateDirectory: camera-fdir
- LimitNOFILE: 4096
- MemoryMax: 512M
- `ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`
- `ReadWritePaths=/var/lib/camera-fdir`, `/run/camera`

**rtp-rgb@cam-rgb.service** (template, instantiated with `cam-rgb`):
- Requires: janus.service
- Conflicts: camera-webrtc-color.service
- ExecStartPre: `/usr/local/bin/cam-wait-capture.sh %i`
- ExecStart: `/usr/local/bin/rtp-rgb.sh /dev/%i`
- EnvironmentFile: `/etc/robot/cam-rgb.env`
- Restart: always (3s)
- StartLimitBurst: 20 per 30s
- SuccessExitStatus: 143 255 SIGTERM
- KillSignal: SIGINT

**janus\_camera\_page\_hook.service:**
- ExecStart: `uvicorn textroom_relay:app --host 0.0.0.0 --port 9000 --workers 1 --no-access-log`
- WorkingDirectory: `/home/boris/robot/janus_camera_page`
- Environment: ROBOT\_URL=http://127.0.0.1:8110/joystick/frame
- Restart: on-failure (1s)

### Depth Node (.55) — Service Dependency Chain

```
                    network.target
                         │
                    ┌────┴────────────┐
                    │                 │
          realsense-mux.service  janus.service
                    │                 │
              ┌─────┴─────┐          │
              │           │          │
        rtp-rgb.service  rtp-depth.service
                                     │
                         janus-camera-page.service
```

**realsense-mux.service:**
- ExecStart: `/usr/bin/python3 realsense_mux.py`
- Creates FIFOs in `/run/realsense/`
- Restart: always

**rtp-rgb.service** (not templated):
- Requires: realsense-mux.service
- After: network.target, realsense-mux.service
- ffmpeg: rawvideo RGB24 480×640 @15fps → H.264 → rtp://127.0.0.1:5003
- Restart: always

**rtp-depth.service:**
- Requires: realsense-mux.service
- After: network.target, realsense-mux.service
- ffmpeg: rawvideo RGB24 480×640 @15fps → H.264 → rtp://127.0.0.1:5004
- Bitrate: 1000k (capped)
- Restart: always

**janus.service:**
- Same binary as color node
- Drop-in: override.conf (v1.3 — hardened, matching color node)
  - `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`
  - `ReadWritePaths=/opt/janus/etc/janus`, `/opt/janus/etc/janus/streams.d`, `/opt/janus/var`

**janus-camera-page.service:**
- After: janus.service
- ExecStart: `/usr/bin/python3 main.py`
- Environment: CAM\_TYPE=depth\_camera
- Resource limits and sandboxing: same as color node (LimitNOFILE, MemoryMax, ProtectSystem)

### Hardware Watchdog

Configuration: `system.conf.d/watchdog.conf` (both nodes)

| Parameter            | Value  |
|----------------------|--------|
| `RuntimeWatchdogSec` | 30s    |
| `RebootWatchdogSec`  | 10min  |

Kernel hardware watchdog: if systemd fails to pet within 30s, kernel forces reboot. If reboot hangs beyond 10 min, hardware reset.

> **Service watchdog (v1.4):** `janus-camera-page.service` uses `Type=notify` + `WatchdogSec=30s`. The Python process sends `WATCHDOG=1` every 15s via raw `$NOTIFY_SOCKET` datagram (no C dependency). If the event loop deadlocks, systemd restarts the service. Janus Gateway (C binary, no sd_notify) is covered by `janus-watchdog.timer` → curls `/janus/info` every 30s and restarts on failure.

### Failsafe Chain (Depth Node)

```
realsense-mux.service crash ──► Restart=always
rtp-{rgb,depth}.service crash ──► OnFailure=realsense-failsafe.service
USB device lost ──► usb-reset-realsense.service
```

---

## 14. SLOs

Cross-ref: [SLO.md](../janus_camera_page/SLO.md)

### Service Level Objectives

| Metric                | Target   | Measurement                                          |
|-----------------------|----------|------------------------------------------------------|
| ICE connect (p95)     | ≤ 5s     | `RTCPeerConnection.iceConnectionState === 'connected'` |
| TTFF (p95)            | ≤ 8s     | Page load → first decoded video frame                 |
| MTTR (p95)            | ≤ 60s    | Fault detection → stream healthy                      |
| Stream availability   | ≥ 99%    | `/health/stream` HTTP 200 (1-minute windows)          |
| Packet loss           | ≤ 1%     | Video inbound RTP loss ratio                          |

### Error Budget

1% unavailability = **7.3 hours/month** downtime.

### Burn-Rate Alerts

| Alert Name                        | Severity | Expression / Condition                                     | for         |
|-----------------------------------|----------|-----------------------------------------------------------|-------------|
| `CamstackStreamUnhealthy`         | critical | `camstack_stream_active == 0`                             | 2 min       |
| `CamstackJanusUnreachable`        | critical | `camstack_janus_reachable == 0`                           | 1 min       |
| `CamstackRecoveryLadderHigh`      | critical | `camstack_recovery_ladder_level >= 3`                     | 30 s        |
| `CamstackModeSafe`                | critical | `camstack_system_mode == 3`                               | 30 s        |
| `CamstackAvailabilityBurnRateFast`| critical | 5-min error rate > 14.4× budget (99% SLO)                 | 2 min       |
| `CamstackWatchdogStalled`         | critical | `rate(camstack_watchdog_checks_total[5m]) == 0`           | 3 min       |
| `CamstackVideoStale`              | warning  | `camstack_video_age_ms > 5000`                            | 1 min       |
| `CamstackModeDegraded`            | warning  | `camstack_system_mode >= 1`                               | 5 min       |
| `CamstackNoClientTelemetry`       | warning  | `camstack_client_last_report_age_seconds > 120`           | 5 min       |
| `CamstackHighPacketLoss`          | warning  | `camstack_client_packet_loss_ratio > 0.01`                | 3 min       |
| `CamstackTTFFRegression`          | warning  | `camstack:ttff_p95:5m > 8`                                | 10 min      |
| `CamstackICEConnectSlow`          | warning  | `camstack:ice_connect_p95:5m > 5`                         | 10 min      |
| `CamstackReconnectStorm`          | warning  | `rate(camstack_ice_connects_total[5m]) > 0.1` (>6/min)    | 5 min       |
| `CamstackAvailabilityBurnRateSlow`| warning  | 1-hour error rate > 6× budget                             | 15 min      |
| `CamstackCPUHot`                  | warning  | `camstack_cpu_temp_celsius > 80`                          | 2 min       |

Recording rules for burn-rate windows: `camstack:stream_availability:{5m,1h,6h}`,
`camstack:watchdog_health_ratio:5m`, `camstack:ttff_p95:5m`, `camstack:ice_connect_p95:5m`.

Source: `janus_camera_page/monitoring/camstack-alert-rules.yml`.

---

## 15. Reference Tables

### Port Allocation — Complete Map

| Port     | Proto | Node(s)     | Service                    | Source Restriction        |
|----------|-------|-------------|----------------------------|---------------------------|
| 22       | TCP   | Both        | SSH                        | Any                       |
| 3478     | UDP/TCP | TURN VPS  | coturn STUN/TURN           | Any                       |
| 443      | TLS   | TURN VPS    | coturn TURNS               | Any                       |
| 5349     | TLS   | TURN VPS    | coturn alt TLS             | Any                       |
| 5002–5120| UDP   | Both        | RTP ingest (ffmpeg→Janus)  | localhost (+ LAN on color)|
| 5003     | UDP   | Depth       | RTP color (realsense→Janus)| 127.0.0.1 only            |
| 5004     | UDP   | Both        | RTP stream (main)          | 127.0.0.1 (+ LAN color)  |
| 5005     | UDP   | Color       | RTCP for mount 1305        | 127.0.0.1                 |
| 7088     | TCP   | Both        | Janus Admin HTTP           | 192.168.1.0/24            |
| 8000     | TCP   | Depth       | RealSense mux HTTP API     | 192.168.1.0/24            |
| 8088     | TCP   | Both        | Janus REST API             | LAN (127/8 + 192.168.1.0/24) |
| 8188     | TCP   | Both        | Janus WebSocket            | LAN (127/8 + 192.168.1.0/24) |
| 8900     | TCP   | Both        | FastAPI camera page        | LAN (127/8 + 192.168.1.0/24) |
| 8989     | TCP   | Both        | Janus WSS (unused†)        | Blocked (no firewall rule)    |
| 9000     | TCP   | Color       | TextRoom relay             | LAN (127/8 + 192.168.1.0/24) |
| 40000–41000 | UDP | Both      | ICE candidates (WebRTC)    | Any                       |
| 49152–65535 | UDP | TURN VPS  | coturn relay range          | Any                       |

> **†Port 8989:** Janus binds WSS on 8989 (`janus.transport.websockets.jcfg`), but no firewall rule opens it on either node. TLS termination is handled by Cloudflare; plain WS on 8188 suffices behind the tunnel. If direct WSS access is needed, add iptables rules and deploy TLS certificates to Janus.

### Environment Variable Master List

| Variable              | Default                        | Used by              |
|-----------------------|--------------------------------|----------------------|
| `CAM_TYPE`            | `"color_camera"`               | settings.py          |
| `CAM_DEVICE`          | `"/dev/cam-rgb"`               | settings.py          |
| `CAM_SERVICE`         | `"rtp-rgb@cam-rgb.service"`    | settings.py          |
| `JANUS_URL`           | `"http://127.0.0.1:8088/janus"`| settings.py          |
| `JANUS_MOUNT_ID`      | `1305`                         | settings.py          |
| `JANUS_TIMEOUT`       | `3.0`                          | settings.py          |
| `DEPTH_CAM_URL`       | `"http://192.168.1.55:8900"`   | settings.py          |
| `SNAPSHOT_PATH`       | `"/run/cam-rgb/snapshot.jpg"`  | settings.py          |
| `TURN_HOST`           | `"82.165.177.194"`             | settings.py          |
| `TURN_PORT`           | `3478`                         | settings.py          |
| `TURN_USER`           | `"webrtc"`                     | settings.py          |
| `TURN_PASS`           | `""`                           | settings.py          |
| `TURN_SHARED_SECRET`  | `""`                           | settings.py          |
| `TURN_CRED_TTL`       | `86400`                        | settings.py          |
| `ICE_POLICY`          | `"all"`                        | settings.py          |
| `WATCHDOG_ENABLED`    | `True`                         | settings.py          |
| `WATCHDOG_INTERVAL`   | `8`                            | settings.py          |
| `WATCHDOG_STALE_MS`   | `10000`                        | settings.py          |
| `WATCHDOG_GRACE`      | `60`                           | settings.py          |
| `MAX_FDIR_REBOOTS`    | `2`                            | settings.py          |
| `CAM_ADMIN_TOKEN`     | `"change-me"`                  | admin.py             |
| `CAM_ADMIN_ENFORCE`   | `1`                            | admin.py             |
| `CAMCTRL_API_KEY`     | `""`                           | dependencies.py      |
| `THERMAL_WARN_C`      | `70`                           | thermal.py           |
| `THERMAL_CRIT_C`      | `80`                           | thermal.py           |
| `THERMAL_RESUME_C`    | `65`                           | thermal.py           |
| `THERMAL_POLL_SEC`    | `10`                           | thermal.py           |
| `FDIR_RING_MAX`       | `500`                          | fdir_events.py       |
| `FDIR_LOG_DIR`        | `"/var/log/camera-fdir"`       | fdir_events.py       |
| `FDIR_PERSIST_MAX_BYTES` | `5242880` (5 MB)            | fdir_events.py       |
| `RS_COLOR_IDX`        | `90`                           | realsense_mux.py     |
| `RS_DEPTH_IDX`        | `18`                           | realsense_mux.py     |
| `RS_IR_IDX`           | `-1`                           | realsense_mux.py     |
| `WIDTH`               | `640`                          | cam-rgb.env          |
| `HEIGHT`              | `480`                          | cam-rgb.env          |
| `FPS`                 | `30`                           | cam-rgb.env          |
| `BITRATE_KBPS`        | `1800`                         | cam-rgb.env          |
| `PRESET`              | `"veryfast"`                   | cam-rgb.env          |
| `TUNE`                | `"zerolatency"`                | cam-rgb.env          |
| `GOP`                 | `30`                           | cam-rgb.env          |
| `PORT`                | `5004`                         | cam-rgb.env          |
| `SNAPSHOT_FPS`        | `1`                            | cam-rgb.env          |
| `ROBOT_URL`           | `"http://127.0.0.1:8110/joystick/frame"` | textroom_relay.py |
| `QUEUE_MAX`           | `50`                           | textroom_relay.py    |

### File Paths

**FIFOs (tmpfs):**

| Path                         | Node  | Content                |
|------------------------------|-------|------------------------|
| `/run/realsense/color.fifo`  | Depth | RGB24 frames → ffmpeg  |
| `/run/realsense/depth.fifo`  | Depth | Colorized depth → ffmpeg |

**State files:**

| Path                                       | Node  | Content                     |
|--------------------------------------------|-------|-----------------------------|
| `/run/camera/fdir_ladder.json`             | Both  | FDIR ladder state (tmpfs)   |
| `/run/camera/fps_profile`                  | Both  | Thermal FPS profile         |
| `/run/cam-rgb/snapshot.jpg`                | Color | Latest JPEG snapshot        |
| `/var/lib/camera-fdir/reboot_count`        | Both  | Persistent reboot counter   |
| `/var/lib/camera-fdir/last_reboot_request` | Both  | Last reboot request JSON    |
| `/var/log/camera-fdir/fdir.jsonl`          | Both  | FDIR event log (append)     |
| `/var/tmp/janus-public-ip.cache`           | Color | Cached public IP            |

**Configuration files:**

| Path                                | Node  | Content                      |
|-------------------------------------|-------|------------------------------|
| `/etc/robot/cam-rgb.env`           | Color | ffmpeg pipeline config       |
| `/etc/robot/camera-secrets.env`    | Color | Admin tokens, TURN secrets   |
| `/etc/robot/janus-nat.json`        | Both  | Cached NAT configuration     |
| `/opt/janus/etc/janus/janus.jcfg`  | Both  | Janus core config            |
| `/opt/janus/etc/janus/janus.plugin.streaming.jcfg` | Both | Mount points   |
| `/opt/janus/etc/janus/janus.transport.http.jcfg` | Both | HTTP transport   |
| `/opt/janus/etc/janus/janus.transport.websockets.jcfg` | Both | WS transport |

### Color vs Depth — Layer-by-Layer Comparison

| Layer            | Color Node (.10)                      | Depth Node (.55)                       |
|------------------|---------------------------------------|----------------------------------------|
| **Camera**       | RealSense D435i                       | RealSense D435                         |
| **USB**          | USB 3.0 (2.0 adapter issue P1.9)      | USB 3.0                                |
| **Driver**       | uvcvideo (kernel)                     | pyrealsense2 (librealsense)            |
| **Device**       | `/dev/cam-rgb` (udev)                 | USB enumeration                         |
| **Capture**      | V4L2 direct (ffmpeg)                  | `run_pipeline()` → FIFO (env-configurable indices) |
| **Pixel format** | YUYV                                  | RGB24 (color), Z16 (depth)             |
| **Resolution**   | 640×480 (configurable)                | 480×640 (portrait, 90° CW)            |
| **FPS**          | 30 (configurable)                     | 15 (fixed)                             |
| **Rotation**     | None                                  | 90° CW in realsense_mux.py            |
| **Encoder**      | ffmpeg libx264 (V4L2 → RTP)          | ffmpeg libx264 (FIFO → RTP) ×2        |
| **Bitrate**      | 1800 kbps (configurable)              | 1500 kbps (color), 1000 kbps (depth)   |
| **RTP dest**     | 127.0.0.1:5004                        | 127.0.0.1:5003 (color), :5004 (depth) |
| **Mount IDs**    | 1305                                  | 1305 (color), 1306 (depth), 1307 (IR)  |
| **Janus name**   | color-node                            | AE-ROBOT-55                            |
| **NAT mapping**  | Self-managed (cron */15, updates `nat_1_1_mapping`) | Copies STUN/TURN coords from color node (no `nat_1_1_mapping` — relay-only) |
| **ICE policy**   | relay (env override)                  | relay (hardcoded)                       |
| **FastAPI port**  | 8900 (LAN; external via Cloudflare)  | 8900 (LAN only)                        |
| **Depth API**    | Proxy via /api/v1/depth\_camera/      | Direct on port 8000                    |
| **TextRoom**     | Port 9000 (relay)                     | N/A                                    |
| **Snapshot**     | ffmpeg -snapshot\_fps 1               | N/A (HTTP API)                         |
| **FDIR USB reset** | N/A                                 | realsense-failsafe.service             |
| **QoS WAN cap** | 20 Mbit (wlan0)                       | 50 Mbit (wlan0)                        |
| **QoS LAN cap** | 100 Mbit (br0)                        | N/A (wlan0 only)                       |
| **Firewall**     | LAN-only (8900/8088/8188/9000); WAN for SSH+ICE UDP | LAN + Tailscale only                   |
| **OOB access**   | SSH (LAN)                             | SSH (LAN) + Tailscale VPN             |
| **3D viewer**    | N/A                                   | DepthApi → DepthProcessor → Three.js   |
| **Watchdog**     | Janus mount age + snapshot mtime      | Janus mount age                        |

### Cross-Reference Documents

| Document                        | Path                                             | Scope                      |
|---------------------------------|--------------------------------------------------|----------------------------|
| Depth Semantic Contract         | `janus_camera_page/DEPTH_SEMANTIC_CONTRACT.md`    | API contract, breaking changes |
| SLO Targets                    | `janus_camera_page/SLO.md`                        | Performance objectives     |
| Reliability Checklist           | `janus_camera_page/RELIABILITY_CHECKLIST.md`      | P0/P1/P2 audit items       |
| Depth Camera Deploy             | `janus_camera_page/DEPTH_CAMERA_DEPLOY.md`        | Pi 5 infrastructure guide  |
| Camera Integration              | `frontend_service/CAMERA.md`                      | Frontend viewer integration|
| Player Safety Laws              | `janus_camera_page/templates/player/docs/SAFETY_LAWS.md` | 24 invariants (L1–L24) |
| Reconnect Flow                  | `janus_camera_page/templates/player/docs/RECONNECT_FLOW.md` | Reconnection sequence |
| Infrastructure Audit Script     | `scripts/audit_camera_stack.sh`                   | 12-checklist live node audit |
| Grafana Dashboard               | `janus_camera_page/monitoring/grafana-dashboard.json` | Provisioning-ready JSON    |
| Alert Rules                     | `janus_camera_page/monitoring/camstack-alert-rules.yml` | Recording + alerting rules |
| External Probe                  | `scripts/browser_canary.py`                       | Browser + HTTP synthetic canary |
| Fault Drill Harness             | `scripts/fault_drills.py`                         | 8 automated fault injection drills |

---

## 16. Observability Stack

All Prometheus metrics are exposed via `GET /metrics` in text exposition format.
Prefix: `camstack_`.  Source: `app/routes/metrics.py`.

### Prometheus Metric Inventory

**Gauges (instantaneous state):**

| Metric                                  | Type  | Description                                      |
|-----------------------------------------|-------|--------------------------------------------------|
| `camstack_system_mode`                  | Gauge | Current mode (0=NOMINAL … 3=SAFE)                |
| `camstack_recovery_ladder_level`        | Gauge | Current FDIR ladder level (0–4)                  |
| `camstack_stream_active`                | Gauge | Watchdog: stream alive (0/1)                     |
| `camstack_janus_reachable`              | Gauge | Janus REST API reachable (0/1)                   |
| `camstack_video_age_ms`                 | Gauge | Age of last video frame (ms, −1 if unknown)       |
| `camstack_cpu_temp_celsius`             | Gauge | SoC temperature (°C, −1 if unavailable)           |
| `camstack_client_packet_loss_ratio`     | Gauge | Latest client-reported loss ratio (0.0–1.0)      |
| `camstack_client_frames_decoded_total`  | Gauge | Latest client cumulative `framesDecoded`          |
| `camstack_client_last_report_age_seconds` | Gauge | Seconds since last `stats_report` telemetry     |

**Counters (cumulative):**

| Metric                                  | Labels              | Description                              |
|-----------------------------------------|---------------------|------------------------------------------|
| `camstack_watchdog_checks_total`        | —                   | Total watchdog cycles                    |
| `camstack_watchdog_healthy_total`       | —                   | Healthy watchdog cycles                  |
| `camstack_watchdog_escalations_total`   | `level`             | Escalations by ladder level              |
| `camstack_fdir_events_total`            | `domain`, `severity`| FDIR events emitted                      |
| `camstack_mode_transitions_total`       | `from_mode`, `to_mode` | System mode transitions               |
| `camstack_ice_connects_total`           | —                   | Client ICE connection events             |

**Histograms:**

| Metric                                  | Buckets (s)                      | Description                     |
|-----------------------------------------|----------------------------------|---------------------------------|
| `camstack_ice_connect_duration_seconds` | 0.5, 1, 2, 3, 5, 8, 10, 15, 30  | ICE connect time (client)       |
| `camstack_ttff_seconds`                 | 1, 2, 3, 5, 8, 10, 15, 20, 30   | Time-to-first-frame (client)    |

### Grafana Dashboard

Provisioning-ready JSON: `janus_camera_page/monitoring/grafana-dashboard.json`

| Row                      | Panels                                                                                    |
|--------------------------|-------------------------------------------------------------------------------------------|
| Stream Health Overview   | Stream Active, Janus Reachable, System Mode, Ladder Level, Video Age, CPU Temp            |
| Client Performance (SLO) | TTFF histogram (p50/p95/p99), ICE histogram, Packet Loss, Frames Decoded, Report Age     |
| FDIR & Recovery          | Watchdog checks/healthy, Escalations by level, Mode transitions, FDIR events, ICE rate   |
| Availability SLO         | 1-hour availability gauge (≥99%), 24-hour trend, Watchdog health ratio                    |

Datasource variable: `${DS_PROMETHEUS}` (type `prometheus`, auto-discover).
UID: `camstack-ops`.

### Alertmanager Rules

Source: `janus_camera_page/monitoring/camstack-alert-rules.yml`

**Recording rules** (group `camstack.recording`, interval 30 s):

| Rule                                 | Expression                                                                       |
|--------------------------------------|-----------------------------------------------------------------------------------|
| `camstack:stream_availability:5m`    | `avg_over_time(camstack_stream_active[5m])`                                       |
| `camstack:stream_availability:1h`    | `avg_over_time(camstack_stream_active[1h])`                                       |
| `camstack:stream_availability:6h`    | `avg_over_time(camstack_stream_active[6h])`                                       |
| `camstack:watchdog_health_ratio:5m`  | `rate(camstack_watchdog_healthy_total[5m]) / rate(camstack_watchdog_checks_total[5m])` |
| `camstack:ttff_p95:5m`              | `histogram_quantile(0.95, rate(camstack_ttff_seconds_bucket[5m]))`                |
| `camstack:ice_connect_p95:5m`       | `histogram_quantile(0.95, rate(camstack_ice_connect_duration_seconds_bucket[5m]))` |

Full alerting-rule table: see [§14 SLOs — Burn-Rate Alerts](#burn-rate-alerts).

### Telemetry Ingestion Pipeline

Browser → `POST /telemetry` → FastAPI → Prometheus gauges/histograms.

| Telemetry event   | Fields consumed                                     | Metrics updated                                          |
|-------------------|------------------------------------------------------|----------------------------------------------------------|
| `ice_connected`   | `duration_ms`                                        | `camstack_ice_connect_duration_seconds`, `camstack_ice_connects_total` |
| `stats_report`    | `packetsLost`, `packetsReceived`, `framesDecoded`    | `camstack_client_packet_loss_ratio`, `camstack_client_frames_decoded_total`, `camstack_client_last_report_age_seconds` |
| `ttff`            | `ttff_ms`                                            | `camstack_ttff_seconds`                                  |

### External Access Path (Cloudflare)

```
Browser → api.techvisioncloud.pl (Cloudflare Tunnel)
       → frontend_service :8401  (/api/{path} catch-all proxy)
       → API Gateway :8201
       → FastAPI :8900  (/healthz, /health/stream, /metrics, /client-config, /janus-ws)
```

> **WSS signaling:** Internet clients reach Janus WebSocket via the same
> Cloudflare → frontend → gateway → FastAPI chain, **not** directly to `:8188`.
> FastAPI's `/janus-ws` (and `/janus/ws`) proxy the connection to
> `ws://127.0.0.1:8188/janus-ws`. Port 8188 is LAN-only per firewall rules.

Tunnel ID: `cdbebf80-c0ec-4c5a-a5de-058b2523cfb1`.
External probe URL: `https://api.techvisioncloud.pl/api/v1/color_camera/`.

---

## 17. Synthetic Probes & Fault Injection

### Synthetic Canary (`scripts/browser_canary.py`)

Two operating modes:

| Mode       | Flag           | Dependencies    | Checks                                                       |
|------------|----------------|-----------------|--------------------------------------------------------------|
| **Browser**| (default)      | Playwright      | ICE connect, TTFF, getStats (loss/FPS/jitter), pass/fail     |
| **HTTP**   | `--http-only`  | `requests`      | `/healthz`, `/health/stream`, `/client-config`, `/metrics`   |

**CLI flags:**

| Flag                | Default                            | Description                          |
|---------------------|------------------------------------|--------------------------------------|
| `--url`             | `http://192.168.1.10:8900/`        | Target URL                           |
| `--timeout`         | 30                                 | Max wait per phase (s)               |
| `--stream-duration` | 10                                 | Stream time before stats (browser)   |
| `--json`            | off                                | JSON-only output (suppress logs)     |
| `--http-only`       | off                                | HTTP probe mode                      |
| `--api-prefix`      | `""`                               | API prefix for external path         |
| `--external`        | off                                | Add `"source": "external"` label    |

**Output schema (both modes):**

```json
{
  "url": "...",
  "pass": true,
  "mode": "browser" | "http",
  "source": "external",   // only with --external
  "duration_s": 4.2,
  "checks": { ... }        // HTTP mode: per-endpoint breakdown
}
```

Exit code: `0` = pass, `1` = fail. Designed for CI `set -e` and cron alerting.

**Recommended cron (VPS):**

```cron
*/5 * * * * /usr/bin/python3 /opt/canary/browser_canary.py --http-only --external --url https://api.techvisioncloud.pl/api/v1/color_camera --json >> /var/log/camstack-canary.jsonl 2>&1
```

### Fault Injection Drills (`scripts/fault_drills.py`)

8 automated drills, each with pre/post state capture and MTTR assertion (≤ 60 s).

| #  | Drill              | Fault injected                        | Expected recovery           | Requires SSH |
|----|--------------------|---------------------------------------|-----------------------------|--------------|
| 1  | `kill_ffmpeg`      | `pkill -9 ffmpeg.*rtp`                | FDIR L1 pipeline restart    | Yes          |
| 2  | `restart_janus`    | `systemctl restart janus`             | FDIR L2 Janus recovery      | Yes          |
| 3  | `kill_realsense`   | `pkill -9 realsense_mux`             | FDIR L1 pipeline restart    | Yes          |
| 4  | `api_restart`      | `POST /action/restart`                | Clean service restart        | No           |
| 5  | `mode_degrade`     | `POST /fdir/mode/degraded`            | Mode transition + promotion  | No           |
| 6  | `flap_depth_link`  | `iptables -I FORWARD -d .55 -j DROP` | DEGRADED → recovery          | Yes          |
| 7  | `hide_tab_resume`  | 30 s telemetry silence                | No crash, no SAFE mode       | No           |
| 8  | `ladder_exhaust`   | Verify ladder structure               | ≥ 4 levels with expected names | No        |

**Drill lifecycle:**

```
reset_ladder() → capture pre-state → inject fault → poll /healthz → capture post-state → assert → reset_ladder()
```

**Modes:**

| Flag          | Behavior                                       |
|---------------|-------------------------------------------------|
| `--host`      | Target node IP (default 192.168.1.10)           |
| `--drill X`   | Run single drill by name                        |
| `--api-only`  | Skip SSH drills; runs: api_restart, mode_degrade, hide_tab_resume, ladder_exhaust |
| `--json`      | JSON-only output for CI                         |
| `--timeout`   | MTTR timeout per drill (default 60 s)           |

**Output schema:**

```json
{
  "target": "http://192.168.1.10:8900",
  "drills_run": 8,
  "passed": 7,
  "failed": 1,
  "results": [
    {"name": "kill_ffmpeg", "passed": true, "recovery_s": 12.3, ...}
  ]
}
```

Exit code: `0` = all pass, `1` = any failure.

### Qualification Gate

v1.7 release gate criteria:

| Gate                              | Tool                               | Threshold           |
|-----------------------------------|--------------------------------------|---------------------|
| All 8 fault drills pass           | `fault_drills.py`                   | 0 failures           |
| External HTTP canary passes       | `browser_canary.py --http-only`     | all 4 checks OK      |
| Browser canary ICE < 5 s          | `browser_canary.py`                 | `ice_connect_ms < 5000` |
| Browser canary TTFF < 8 s         | `browser_canary.py`                 | `ttff_ms < 8000`    |
| 202 unit/integration tests pass   | `pytest janus_camera_page/tests/`   | 0 new failures       |
| Grafana dashboard imports cleanly | Manual                              | All panels render    |
| Alert rules load in Prometheus    | `promtool check rules`              | 0 errors             |

---

## Changelog

### v1.8 (2026-03-06) — Depth Node Alignment

Code + spec alignment for .55 audit findings (13-point review against depth node archive).

**Code changes:**
- **`GET /status`**: New full-system diagnostic endpoint combining health, system mode, recovery ladder, service status, and settings snapshot. Route table now lists `/healthz`, `/health/stream`, and `/status`.
- **DEPTH_CAMERA_DEPLOY.md**: Fixed ICE Policy from `all` (wrong) to `relay` — code hardcodes relay for `depth_camera` type. Added `CAM_SNAPSHOT_WATCHDOG=0` to `watchdog.conf` template to prevent monitoring a snapshot file that depth nodes don’t produce.

**Spec corrections:**
- **`/depth` response contract**: Changed from `{"depth_m", "x_norm", "y_norm"}` to actual `{"type": "depth", "x", "y", "depth"}` matching `realsense_mux.py` `DepthResponse` model.
- **`/depth/frame_color_overlay` contract**: Changed from `depth_data + mapped_color + depth_capture_ts_ms` to actual `rgb_data + rgb_dtype + depth_data + depth_dtype + timestamp` matching FastAPI code. Added note about alias support in 3D viewer (`depth-api.ts`).
- **DepthApi decoder reference**: Updated from `Uint16Array + mapped_color` to `float32 + rgb_data` with alias fallback.

**Verified already correct in workspace (not in .55 archive):**
CORS regex, admin enforcement, `/health/stream`, deep `/healthz`, full telemetry pipeline (ttff/stats_report/ice_connected → `camstack_*` metrics), 204 response fix, `RS_COLOR_IDX`/`RS_DEPTH_IDX`/`RS_IR_IDX` env vars, FIFO consecutive failure counter, sd_notify integration.

### v1.7.3 (2026-03-06) — Final Consistency Pass

Documentation-only. Last 3 contradictions identified against deployed code.

1. **Timeout Contract direction**: Janus row said `≥ sessionTimeoutMs / 1000`, player row said `≤ session_timeout × 1000`, invariant said `≤`. Unified to `=` (current deployment has exact equality: 60 s = 60 000 ms). Invariant now reads `noFrameThresholdMs / 1000 < no_media_timer < session_timeout = sessionTimeoutMs / 1000`.
2. **Telemetry Pipeline `ice_connected` row**: Added missing `camstack_` prefix to `ice_connect_duration_seconds` and `ice_connects_total` — matching `app/routes/metrics.py` declarations.
3. **Changelog date chronology**: v1.7.1 and v1.7 dates normalized to 2026-03-06 (all three versions produced on the same day).

### v1.7.2 (2026-03-06) — Spec Cleanup (4-point follow-up)

Documentation-only. Addresses remaining inconsistencies identified in
the follow-up review of v1.7.1.

1. **Canonical external WSS path**: Replaced misleading `wss://api.techvisioncloud.pl/janus-ws (Cloudflare → :8188)` in Network Topology with correct full chain: `Cloudflare → frontend :8401 → Gateway :8201 → FastAPI :8900 /janus-ws → ws://127.0.0.1:8188/janus-ws`. Updated External Access Path section to list `/janus-ws` and added callout clarifying that Internet clients never reach `:8188` directly.
2. **Timeout Contract `session_timeout` constraint**: Fixed `≥ player sessionTimeoutMs` (unit mismatch) → `≥ player sessionTimeoutMs / 1000`. Now consistent with the invariant and the player row.
3. **`camstack_` metric prefixes**: Added missing prefix to three alert/recording-rule expressions (`client_last_report_age_seconds`, `client_packet_loss_ratio`, `watchdog_healthy_total/watchdog_checks_total`) and Telemetry Pipeline table — now matches both Prometheus Metric Inventory and `camstack-alert-rules.yml`.
4. **Port Allocation table**: Moved ICE range (40000–41000) and coturn relay range (49152–65535) rows above the †8989 footnote so the markdown table renders correctly.

### v1.7.1 (2026-03-06) — Spec Accuracy Pass (9-point review)

Documentation-only release. Researched 9 expert-identified contradictions
against actual code/config and corrected the spec where inaccurate.

1. **Network Topology diagram**: Removed incorrect "direct or TURN relay" label on WS path. TURN relays ICE/WebRTC media, not WS signaling. Replaced with three-line breakdown: LAN (direct WS), Internet (Cloudflare WSS), Media (TURN relay).
2. **Port Allocation table**: 8088/8188/8900/9000 changed from "Any" to "LAN (127/8 + 192.168.1.0/24)" matching actual `firewall-{color,depth}.sh` iptables rules.
3. **CSP frame-ancestors**: Added missing `https://blupassionsystem.de:8443` to spec §6 Security Headers table (was deployed in v1.5 code but spec not updated).
4. **FastAPI TURN role**: Corrected Component Roles table — FastAPI **does** generate/issue ephemeral TURN credentials (`generate_turn_credentials()` → `/client-config`). Moved "ephemeral TURN credential issuance" to Owns column; TURN VPS now says "validation" not "credentials".
5. **ICE_POLICY**: Clarified that `"relay"` in ICE Transport Policy table is the production env value; settings.py code default is `"all"`. Both the Settings table and Env Var Master List already correctly stated `"all"`.
6. **Depth NAT mapping**: Added "(color only)" to `nat_1_1_mapping` in NAT Config table. Clarified Color vs Depth comparison: depth copies STUN/TURN coords but has no `nat_1_1_mapping` (relay-only ICE).
7. **Visibility timeout**: Fixed stale "30s" → "60 s" in Tab Visibility table. Was already correct in Timeout Contract (60000 ms) and code since v1.5; this table was missed.
8. **SLO availability**: Changed measurement from `/healthz` to `/health/stream` — the endpoint specifically designed for stream availability measurement (v1.6).
9. **Port 8989 (WSS)**: Changed from "Any" to "Blocked (no firewall rule)" with footnote explaining Janus binds it but no iptables rule opens it; Cloudflare handles TLS. Also fixed Color vs Depth table: `8900 (public)` → `8900 (LAN; external via Cloudflare)`, `Firewall: Open to internet` → `LAN-only (8900/8088/8188/9000); WAN for SSH+ICE UDP`.

### v1.7 (2026-03-06) — External Probe, Fault Drills, Observability Stack

Operational qualification artifacts: external synthetic probe, automated fault
injection matrix, Grafana dashboard, and Alertmanager burn-rate alerting.

**External Synthetic Probe:**
- `scripts/browser_canary.py` now supports **HTTP-only mode** (`--http-only`) for VPS cron monitoring without Playwright/Chromium. Checks `/healthz`, `/health/stream`, `/client-config`, `/metrics` with per-check pass/fail verdicts.
- **External mode** (`--external --api-prefix /api/v1/color_camera`) for probing through Cloudflare tunnel (`api.techvisioncloud.pl`).
- Both browser and HTTP modes output structured JSON with `{"pass": bool, "mode": "browser"|"http", "source": "external"}` for CI integration.

**Fault Injection Automation:**
- `scripts/fault_drills.py` — 8 automated drills: `kill_ffmpeg`, `restart_janus`, `kill_realsense`, `api_restart`, `mode_degrade`, `flap_depth_link`, `hide_tab_resume`, `ladder_exhaust`.
- Each drill captures pre/post FDIR state, measures recovery time, asserts MTTR ≤ 60 s SLO.
- `--api-only` mode for 4 drills that work without SSH (safe for external execution).
- Ladder reset between drills ensures clean isolation.

**Grafana Dashboard:**
- `janus_camera_page/monitoring/grafana-dashboard.json` — provisioning-ready dashboard with 4 row groups:
  - Stream Health Overview: stream active, Janus reachable, system mode, ladder level, video age, CPU temp.
  - Client Performance (SLO): TTFF histogram (p50/p95/p99), ICE connect histogram, packet loss, frames decoded, client report age.
  - FDIR & Recovery: watchdog checks vs healthy, escalations by level, mode transitions, FDIR events by domain/severity, ICE connection rate.
  - Availability SLO: 1-hour stream availability gauge, 24-hour trend, watchdog health ratio.

**Alertmanager Rules:**
- `janus_camera_page/monitoring/camstack-alert-rules.yml` — recording rules + 13 alerting rules:
  - Critical: `CamstackStreamUnhealthy` (stream down >2 min), `CamstackJanusUnreachable` (>1 min), `CamstackRecoveryLadderHigh` (L3+), `CamstackModeSafe`, `CamstackAvailabilityBurnRateFast` (14.4× error budget), `CamstackWatchdogStalled`.
  - Warning: `CamstackVideoStale` (>5 s), `CamstackModeDegraded` (>5 min), `CamstackNoClientTelemetry` (>2 min), `CamstackHighPacketLoss` (>1%), `CamstackTTFFRegression` (p95 >8 s), `CamstackICEConnectSlow` (p95 >5 s), `CamstackReconnectStorm` (>6/min), `CamstackAvailabilityBurnRateSlow` (6× budget), `CamstackCPUHot` (>80°C).
  - Recording rules: `camstack:stream_availability:{5m,1h,6h}`, `camstack:watchdog_health_ratio:5m`, `camstack:ttff_p95:5m`, `camstack:ice_connect_p95:5m`.

### v1.6 (2026-03-06) — Architecture Roles, Timeout Contract, E2E Stream Health

Response to expert review on role separation, implicit contracts, and provable E2E health.

**Architecture:**
- **Component role table**: Formalized strict role separation — Cloudflare (control plane ingress), TURN VPS (media relay), Janus (media broker), FastAPI (gateway + control node), .55 (subordinate sensor). Each component's "owns" vs "does not own" is now explicit. Added failure isolation rules.
- **Timeout contract table**: Cross-layer source-of-truth linking Janus `session_timeout`, player `sessionTimeoutMs`, `noFrameThresholdMs`, watchdog intervals, systemd `WatchdogSec`, and TURN credential TTL. Key invariant documented: `noFrameThresholdMs < no_media_timer < session_timeout ≤ sessionTimeoutMs/1000`.
- Fixed `sessionTimeoutMs` in spec table from 30000 → 60000 (was already correct in code since v1.5, but spec table was stale).

**E2E Stream Health:**
- **`GET /health/stream`**: New endpoint that goes beyond process liveness. Checks: (1) Janus mount RTP freshness, (2) client telemetry recency + framesDecoded, (3) system mode, (4) recovery ladder level. Returns 200 when stream is usable, 503 when degraded. Designed for synthetic probes and external monitoring.

**Prometheus Metrics (new):**
- `camstack_ttff_seconds` histogram — time-to-first-frame from client telemetry (buckets: 1–30 s).
- `camstack_client_packet_loss_ratio` gauge — latest client-reported loss ratio.
- `camstack_client_frames_decoded_total` gauge — latest cumulative framesDecoded.
- `camstack_client_last_report_age_seconds` gauge — seconds since last `stats_report`.
- Telemetry ingestion (`POST /telemetry`) now feeds all new metrics from `stats_report` and `ice_connected` events.

**Synthetic Browser Canary:**
- `scripts/browser_canary.py` — Playwright-based headless probe. Opens player page, hooks `RTCPeerConnection`, measures ICE connect time, TTFF, streams for N seconds, then collects `getStats()` (framesDecoded, packetsLost, FPS, RTT, jitter). JSON output with pass/fail verdict against SLO thresholds. Exit code 0/1 for CI integration.

**Infrastructure (deploy):**
- `.10 override.conf`: Added `Environment=PYTHONPATH=/home/boris/robot` and switched `ExecStart` to venv Python (`/home/boris/robot/.venv/bin/python`) to resolve `shared_config` and `pydantic-settings` imports. Service now starts cleanly.
- `.55`: Deployed CSP frame-ancestors update with `https://blupassionsystem.de:8443` (compatible with .55's older dataclass-based settings).

### v1.5 (2026-03-06) — Review P0–P1 Fixes: CSP, CORS, Admin Hard-Fail, Client Timeout

Direct response to 6 reviewer findings on the v1.4 branch.

**P0 — Security:**
- **CSP frame-ancestors**: Replaced invalid CIDR `http://192.168.1.0/24` with exact origins `http://192.168.1.10:8900 http://192.168.1.55:8900`. CSP spec requires scheme+host+port, not CIDR. Env override: `CSP_FRAME_ANCESTORS_LAN`.
- **CORS**: Replaced `allow_origins` list containing wildcard patterns (`http://localhost:*`) with `allow_origin_regex` — Starlette applies `re.fullmatch()`. Regex covers `localhost`, `127.0.0.1`, `192.168.1.*`, and `*.techvisioncloud.pl`. Env override: `CORS_ORIGIN_REGEX`.
- **Admin auth hard-fail**: When `CAM_ADMIN_ENFORCE=1` (default) and `CAM_ADMIN_TOKEN` is still `"change-me"`, admin routes now return **503** (not 403) with an explicit message demanding a real token. Previously the default token was silently accepted.

**P1 — Server/Client Contract:
- **Player session timeout**: `config.js` default changed from 30000 ms to 60000 ms matching Janus `session_timeout=60`. Both `color_view.html` and `depth_view.html` now pass `data-session-timeout-ms="60000"`. Prevents premature HARD reconnect after tab-hidden recovery.

**P1 — Attack Surface:**
- **Firewall**: Ports 8900 (FastAPI) and 9000 (TextRoom relay) restricted to `127.0.0.0/8` + `192.168.1.0/24` on color node. Consistent with Janus 8088/8188 restrictions — all external access routes through Cloudflare tunnel.

**P2 — Reproducibility:**
- **Test bootstrap**: `conftest.py` now adds monorepo root (`..`) to `sys.path`, making `shared_config.network` importable when running `pytest` standalone from `janus_camera_page/`. All 91 tests pass in both monorepo and standalone mode.

### v1.4 (2026-03-07) — External Review: Attack Surface Reduction & Service Watchdog

Addresses 9-area external expert review covering physical resilience, network exposure, iframe embedding, admin auth, service monitoring, and session tuning.

**Security — Attack Surface:**
- Firewall: Janus REST (8088) and WebSocket (8188) restricted to `127.0.0.0/8` + `192.168.1.0/24` on both nodes. External browser access routes through Cloudflare tunnel → localhost. Eliminates direct internet exposure of Janus signaling.
- Admin auth: `CAM_ADMIN_ENFORCE` default changed from `0` (warn) to `1` (reject 403). Production deployments now enforce admin token by default; set `CAM_ADMIN_ENFORCE=0` explicitly for local development.

**Security — Headers:**
- Removed `X-Frame-Options: SAMEORIGIN` header. CSP `frame-ancestors 'self' https://*.techvisioncloud.pl {LAN}` is the modern replacement and was already present. Having both created a contradiction: SAMEORIGIN blocked cross-origin iframe embedding that frame-ancestors explicitly allowed.

**Reliability — Service Watchdog:**
- `janus-camera-page.service`: Changed `Type=simple` → `Type=notify` with `WatchdogSec=30s`. Python code sends `READY=1` on startup and `WATCHDOG=1` every 15s via raw `$NOTIFY_SOCKET` datagram (no C dependency). Event loop deadlock → systemd auto-restart.
- `janus-watchdog.timer` (new, both nodes): systemd timer fires every 30s, curls `http://127.0.0.1:8088/janus/info`. Failure → restarts `janus.service`. `BindsTo=janus.service` ensures timer lifecycle follows Janus.

**Reliability — Session Tuning:**
- `session_timeout`: Bumped from 30s to 60s (Janus default) on both nodes. 30s was too aggressive for satellite/lossy links — packets delayed >15s beyond last keepalive caused unnecessary full-session teardown + reconnect. The JS player's `pingpong` interval (every ~10s) remains well within the 60s timeout window.

**Not addressed (requires hardware / architecture decisions):**
- USB 2.0 degradation on .10 (need powered USB 3.0 hub)
- .55 PSU (need regulated 5V/5A)
- .10 SPOF (needs OOB management via Tailscale or secondary gateway)
- .55 Wi-Fi → wired (hardware change)
- Synthetic browser canary (future: headless-Chrome Prometheus exporter)

### v1.3 (2026-03-06) — Concurrency Safety, Memory Leaks & Infrastructure Hardening

Third-pass audit: 16 verified issues across Python services, JS player, Janus configs, and systemd.

**Python Services — Concurrency:**
- `relay_proxy.py`: Added `asyncio.Lock` protecting `_client` initialization — prevents race where concurrent `relay_get()` calls create duplicate `httpx.AsyncClient` instances (first leaked, never closed). Matches existing pattern in `janus_proxy.py`.
- `janus.py` `with_streaming_handle`: Added 30s `concurrent.futures.ThreadPoolExecutor` timeout — if wrapped function hangs (e.g., Janus unresponsive), timeout fires and cleanup runs. Prevents permanent session+handle leaks.
- `janus.py` `janus_detach()`/`janus_destroy()`: Now return `bool` (True on success, False on failure). Caller in `_wrapper()` logs `ERROR`-level messages on cleanup failure, making orphaned Janus sessions observable.

**Python Services — Robustness:**
- `janus.py` `janus_summary()`: Added top-level `try/except` returning empty dict on any exception. Belt-and-suspenders guard against future Janus API structural changes (existing per-level guards retained).
- `recovery_ladder.py` `_load_ladder_state()`: Added `fcntl.flock(LOCK_SH)` on file reads for consistency with the locked reboot counter pattern. Prevents reading partial state during concurrent writes.
- `telemetry.py`: Fixed pre-existing FastAPI assertion error — `status_code=204` endpoint now uses `response_class=Response` and returns `Response(status_code=204)` instead of `None`.

**Browser Player — Memory Leaks:**
- `janus_streaming_adapter.js`: Constructor's `session.onEvent()` subscription now stores the unsubscribe handle; `stop()` calls it. Prevents callback accumulation across `RECREATE_SESSION` cycles.
- `janus_textroom_adapter.js`: Same fix — `detach()` now unsubscribes from session events.
- `dom_ui_adapter.js`: Added `destroy()` method that removes all 4 DOM event listeners (`click` ×2, `stalled`, `waiting`) and clears active timers. Previously no cleanup existed.

**Infrastructure — Janus Configs:**
- `color_node/janus.plugin.streaming.jcfg`: Replaced `secret = "changeme"` with `__JANUS_STREAMING_SECRET__` placeholder (matches `__JANUS_ADMIN_SECRET__` pattern used elsewhere).
- `depth_node/janus.transport.http.jcfg`: Fixed `admin_https = true` → `false` (depth admin is localhost-only; TLS without certs was broken). Now consistent with color node.
- Both nodes `janus.jcfg`: Reduced `debug_level = 2` → `1` for production (warnings + errors only).

**Infrastructure — systemd:**
- Color node `janus.service.d/`: Consolidated 4 conflicting drop-ins (`disable-sandbox.conf`, `secure-paths.conf`, `ensure-streams.conf`, `zzz-disable-ensure-streams.conf`) into single `override.conf`. Final state: `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes` with explicit `ReadWritePaths` for Janus data directories.
- Color node `janus-camera-page.service.d/override.conf`: Added `LimitNOFILE=4096`, `MemoryMax=512M`, `ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`, `ReadWritePaths` for FDIR state.
- Depth node `janus.service.d/`: Replaced `disable-sandbox.conf` (disabled all sandboxing) with same hardened config as color node.

**Infrastructure — TURN:**
- `turnserver.conf`: Set `bps-capacity=10000000` (10 Mbps per TURN allocation). Was `0` (unlimited), providing no DDoS bandwidth protection.

### v1.2 (2026-03-05) — Deep Hardening & Legacy Cleanup

Comprehensive audit: 38+ issues across 6 domains; all P0/P1/P2 code fixes shipped, legacy service removed.

**FDIR — Thread Safety & Atomics:**
- `RecoveryLadder` methods serialized via `threading.Lock` — prevents concurrent watchdog+API corruption
- Reboot counter read/write under `fcntl.flock(LOCK_EX)` — prevents race between reader (API) and writer (reboot)
- State persistence via `tempfile` + `os.fsync()` + `os.rename()` — crash-safe writes
- Retry-before-escalation: watchdog retries Janus handle once (2s backoff) before ladder escalation
- Dedup window switched to monotonic timestamp (was `threading.Event`) — immune to spurious wake

**FDIR — Operational:**
- `fdir.jsonl` size-based log rotation: 5 MB max, 1 backup (`FDIR_PERSIST_MAX_BYTES` env)
- `system_mode.py` listener timeout: 5s via `ThreadPoolExecutor` — hanging listener cannot stall transitions
- `thermal.py` specific exception handling: `PermissionError` / `FileNotFoundError` instead of blanket catch
- Subprocess timeouts increased: pipeline restart 15s→45s, Janus restart 20s→60s

**FastAPI — Proxy Safety:**
- `janus_proxy.py` and `depth_camera_proxy.py` guard `httpx.AsyncClient` lifecycle with `asyncio.Lock`
- `janus.py` correctly traverses double-nested `data.info.info` Janus response structure; safe defaults for missing keys

**Browser Player — Race Conditions:**
- `JanusSessionManager._lifecycleMutex` serializes `init()` / `destroy()` — no more zombie sessions
- `JanusStreamingAdapter._enqueue()` catches + logs errors (was silent); `recreate()` rejects dangling `_pendingWatch`
- `WatchdogService` FPS ring span: `count → count-1` intervals — fixes ~1.7% FPS inflation
- `ReconnectCoordinator` settle timeout: token + `shouldContinue()` guard before `_scheduleNext()`

**Encoding:**
- Depth node `rtp-rgb.service`: added `-b:v 1500k -maxrate 1500k -bufsize 3000k` (was uncapped CRF)

**Legacy Cleanup:**
- `proxt_camera_service/` deleted (zero runtime dependencies, replaced by `janus_camera_page/`)
- Removed from `Makefile`, `scripts/run_all_tests.sh`, `.github/workflows/ci.yml` (4 locations)

**Infrastructure Tooling:**
- New `scripts/audit_camera_stack.sh` — 500-line bash script covering checklists 1–12, runs on live nodes
- Audit results: color node 48 PASS / 3 WARN / 0 FAIL; depth node 50 PASS / 2 WARN / 0 FAIL

### v1.1 (2026-03-05) — Reliability Hardening

Fixes applied from P0/P1 audit (47 issues evaluated, 15 code fixes shipped):

**CRITICAL (P0):**
- **P0-1:** `_safe_write()` FIFO recovery tracks consecutive failures per channel; escalates to `RuntimeError` after 10 failures (systemd restart)
- **P0-2:** `janus-nat.json` and `janus.jcfg` writes are now atomic (`tempfile` + `fsync` + `rename`)
- **P0-3/4:** API Gateway circuit breaker records upstream 5xx as **failure** (was incorrectly recorded as success)
- **P0-5:** API Gateway WS proxy records `cb.record_failure()` on session timeout (was silent)
- **P0-6:** `h.send({request:'start'})` now has an error callback — Janus SDP rejection emits ERROR event instead of being silently lost
- **P0-7:** `_settleStartTimeoutTimer` cleared in reconnect catch block — prevents stale timer scheduling duplicate attempt
- **P0-9:** Camera mode indices (`color_idx`, `depth_idx`, `ir_idx`) configurable via `RS_COLOR_IDX`, `RS_DEPTH_IDX`, `RS_IR_IDX` env vars
- **P0-10:** `__post_init__()` logs WARNING if neither `TURN_PASS` nor `TURN_SHARED_SECRET` is set
- **P0-11:** Janus session `success`/`error`/`destroyed` callbacks guarded with `_gen !== initGen` — prevents stale `SESSION_READY` after init timeout

**HIGH (P1):**
- **P1-1:** API Gateway `cam_read_timeout` raised from 8s to 30s (depth camera needs longer for large frames)
- **P1-3:** Circuit breaker HALF_OPEN state now has probe timeout — hanging probe reverts to OPEN after `recovery_timeout_s`
- **P1-6:** Janus `detach`/`destroy` failure logs upgraded from `debug` to `warning` (session leaks now visible at default log level)
- **P1-7:** `STREAM_RECOVERED` in CONNECTING state preserves `snap.firstFrameReceived` (was hardcoding `true`, violating L4 invariant)
- **P1-10:** `WatchdogService.start()` clears stale interval before creating new one (idempotent, prevents timer leak)
