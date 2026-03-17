#!/usr/bin/env bash
set -euo pipefail

DEV="${1:-/dev/cam-rgb}"
ENV_FILE="/etc/robot/cam-rgb.env"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-1800}"
GOP="${GOP:-$FPS}"
PRESET="${PRESET:-veryfast}"
TUNE="${TUNE:-zerolatency}"
PORT="${PORT:-5004}"

PIX_FMT="${PIX_FMT:-yuyv422}"

SNAPSHOT_PATH="${SNAPSHOT_PATH:-/run/cam-rgb/snapshot.jpg}"
SNAPSHOT_FPS="${SNAPSHOT_FPS:-1}"

BITRATE_BPS=$(( BITRATE_KBPS * 1000 ))

STALE_THRESHOLD="${STALE_THRESHOLD:-20}"

install -d -m 0775 /run/cam-rgb
# Remove stale snapshot so watchdog doesn't trigger on old data
rm -f "$SNAPSHOT_PATH"

command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found"; exit 1; }
[ -e "$DEV" ] || { echo "device $DEV not found"; exit 1; }

# Launch ffmpeg in background so we can monitor snapshot freshness
/usr/bin/ffmpeg -y -nostdin -hide_banner -loglevel warning \
  -f v4l2 -input_format "$PIX_FMT" -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" -i "$DEV" \
  -filter_complex "[0:v]split=2[venc][vsnap_in];[vsnap_in]fps=${SNAPSHOT_FPS},scale=${WIDTH}:${HEIGHT},format=yuv420p[vsnap]" \
  \
  -map "[venc]" -an -c:v libx264 -preset "$PRESET" -tune "$TUNE" -pix_fmt yuv420p -profile:v baseline \
  -b:v "${BITRATE_BPS}" -maxrate "${BITRATE_BPS}" -bufsize "${BITRATE_BPS}" \
  -g "$GOP" -x264-params keyint="$GOP":min-keyint="$GOP":scenecut=0:open_gop=0:repeat-headers=1 \
  -f rtp "rtp://127.0.0.1:${PORT}?pkt_size=1200" \
  \
  -map "[vsnap]" -an -c:v mjpeg -q:v 5 -f image2 -update 1 "$SNAPSHOT_PATH" &

FFPID=$!

# Grace period: wait for first snapshot to appear
sleep 10

# Snapshot freshness watchdog: kill ffmpeg if snapshot stops updating
while kill -0 "$FFPID" 2>/dev/null; do
  sleep 5
  if [ -f "$SNAPSHOT_PATH" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$SNAPSHOT_PATH") ))
    if [ "$AGE" -gt "$STALE_THRESHOLD" ]; then
      echo "[watchdog] snapshot stale ${AGE}s (threshold ${STALE_THRESHOLD}s), killing ffmpeg" >&2
      kill -9 "$FFPID" 2>/dev/null || true
      exit 1  # systemd Restart=always will restart us
    fi
  fi
done

wait "$FFPID"
