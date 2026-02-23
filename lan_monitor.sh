#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────
# lan_monitor.sh — continuous LAN health probe for robot movement
#
# Usage:
#   ./lan_monitor.sh start    — run as background daemon (survives SSH drop)
#   ./lan_monitor.sh stop     — stop the daemon
#   ./lan_monitor.sh status   — show if running + last 20 lines
#   ./lan_monitor.sh events   — show only anomalies (LOST / SLOW / WARN)
#   ./lan_monitor.sh tail     — live tail of the log
#   ./lan_monitor.sh          — same as 'start'
# ───────────────────────────────────────────────────────────────────

# ── targets ──
declare -A TARGETS=(
  [gateway]=192.168.1.109
  [symovo]=192.168.1.100
  [xarm]=192.168.1.220
  [igus]=192.168.1.230
  [depth_cam]=192.168.1.55
)

INTERVAL=1            # seconds between rounds
WARN_MS=50            # yellow threshold (ms)
FAIL_MS=500           # red threshold / packet loss

LOG_DIR="/tmp/lan_monitor"
LOG_FILE="${LOG_DIR}/probe.log"
EVENT_FILE="${LOG_DIR}/events.log"
PID_FILE="${LOG_DIR}/monitor.pid"

mkdir -p "$LOG_DIR"

# ── daemon loop (called internally) ──
_run_loop() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') === LAN Monitor started (pid $$) ===" >> "$LOG_FILE"
  echo "$(date '+%Y-%m-%d %H:%M:%S') === LAN Monitor started (pid $$) ===" >> "$EVENT_FILE"
  echo $$ > "$PID_FILE"

  while true; do
    ts=$(date '+%H:%M:%S')
    line="[$ts]"

    for name in gateway symovo xarm igus depth_cam; do
      ip=${TARGETS[$name]}
      result=$(ping -c 1 -W 1 "$ip" 2>&1)
      if echo "$result" | grep -q "100% packet loss"; then
        line+=" ${name}:LOST"
        echo "$(date '+%Y-%m-%d %H:%M:%S') LOST $name ($ip)" >> "$EVENT_FILE"
      else
        ms=$(echo "$result" | grep "time=" | sed 's/.*time=\([0-9.]*\).*/\1/')
        ms_int=${ms%%.*}
        if [ "$ms_int" -ge "$FAIL_MS" ] 2>/dev/null; then
          line+=" ${name}:${ms}ms!"
          echo "$(date '+%Y-%m-%d %H:%M:%S') SLOW $name ($ip) ${ms}ms" >> "$EVENT_FILE"
        elif [ "$ms_int" -ge "$WARN_MS" ] 2>/dev/null; then
          line+=" ${name}:${ms}ms~"
          echo "$(date '+%Y-%m-%d %H:%M:%S') WARN $name ($ip) ${ms}ms" >> "$EVENT_FILE"
        else
          line+=" ${name}:${ms}ms"
        fi
      fi
    done

    echo "$line" >> "$LOG_FILE"
    sleep "$INTERVAL"
  done
}

# ── commands ──
cmd="${1:-start}"

case "$cmd" in
  start)
    # kill old instance if running
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Stopping old instance (pid $(cat "$PID_FILE"))..."
      kill "$(cat "$PID_FILE")" 2>/dev/null
      sleep 1
    fi
    # rotate logs if > 5MB
    for f in "$LOG_FILE" "$EVENT_FILE"; do
      [ -f "$f" ] && [ "$(stat -c%s "$f" 2>/dev/null || echo 0)" -gt 5242880 ] && mv "$f" "${f}.old"
    done
    echo "Starting LAN monitor daemon..."
    nohup bash "$0" __daemon__ > /dev/null 2>&1 &
    sleep 1
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Running (pid $(cat "$PID_FILE"))"
      echo "  Log:    $LOG_FILE"
      echo "  Events: $EVENT_FILE"
      echo ""
      echo "Commands:  $0 status | events | tail | stop"
    else
      echo "Failed to start!"
      exit 1
    fi
    ;;

  __daemon__)
    _run_loop
    ;;

  stop)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")"
      echo "Stopped (pid $(cat "$PID_FILE"))"
      rm -f "$PID_FILE"
    else
      echo "Not running"
    fi
    ;;

  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "RUNNING (pid $(cat "$PID_FILE"))"
    else
      echo "NOT RUNNING"
    fi
    echo ""
    echo "=== Last 20 probe lines ==="
    tail -20 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
    echo ""
    echo "=== Last 10 events ==="
    tail -10 "$EVENT_FILE" 2>/dev/null || echo "(no events)"
    ;;

  events)
    if [ -f "$EVENT_FILE" ]; then
      cat "$EVENT_FILE"
    else
      echo "(no events yet)"
    fi
    ;;

  tail)
    tail -f "$LOG_FILE" 2>/dev/null
    ;;

  *)
    echo "Usage: $0 {start|stop|status|events|tail}"
    exit 1
    ;;
esac
