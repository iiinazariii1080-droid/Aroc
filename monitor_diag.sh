#!/usr/bin/env bash
# Diagnostic monitoring script for robot freeze investigation
# Collects CPU, RAM, TCP connections, and Docker stats every 5 seconds
# Usage: ./monitor_diag.sh [interval_seconds]
# Stop with Ctrl+C

INTERVAL=${1:-5}
LOG="/home/boris/robot/diag_$(date +%Y%m%d_%H%M%S).log"

echo "=== Robot Diagnostic Monitor ==="
echo "Log file: $LOG"
echo "Interval: ${INTERVAL}s"
echo "Press Ctrl+C to stop"
echo ""

# Trap Ctrl+C to print summary
trap 'echo ""; echo "=== Monitoring stopped at $(date -Iseconds) ==="; echo "Log saved to: $LOG"; exit 0' INT

SAMPLE=0
while true; do
    SAMPLE=$((SAMPLE + 1))
    TS=$(date -Iseconds)

    {
        echo "================================================================"
        echo "=== SAMPLE #${SAMPLE} @ ${TS} ==="
        echo "================================================================"

        # --- CPU & Load ---
        echo ""
        echo "--- CPU / Load Average ---"
        cat /proc/loadavg
        # Per-core CPU usage snapshot
        top -bn1 -w 120 | head -8

        # --- RAM ---
        echo ""
        echo "--- Memory ---"
        free -h

        # --- TCP Connections ---
        echo ""
        echo "--- TCP Connections Summary ---"
        echo "State counts:"
        ss -tna | tail -n +2 | awk '{print $1}' | sort | uniq -c | sort -rn
        echo ""
        echo "Active connections to key services:"
        ss -tna | grep -E ':7900|:7905|:8101|:8102|:8110|:8201|:8401|:502|:1883' || echo "(none)"

        # --- Docker Stats ---
        echo ""
        echo "--- Docker Container Stats ---"
        docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}" 2>/dev/null

        # --- Container Health ---
        echo ""
        echo "--- Container Status ---"
        docker ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null

        # --- Disk I/O ---
        echo ""
        echo "--- Disk I/O (mmcblk0) ---"
        cat /proc/diskstats | grep mmcblk0 | head -1

        echo ""
    } >> "$LOG" 2>&1

    # Print brief summary to terminal
    LOAD=$(cat /proc/loadavg | awk '{print $1, $2, $3}')
    MEM=$(free -h | awk '/^Mem:/{print $3 "/" $2}')
    TCP=$(ss -tna | tail -n +2 | wc -l)
    CONTAINERS=$(docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null | tr '\n' ' ')
    printf "[#%04d %s] Load: %-15s | RAM: %-12s | TCP: %-4s | %s\n" \
        "$SAMPLE" "$(date +%H:%M:%S)" "$LOAD" "$MEM" "$TCP" "$CONTAINERS"

    sleep "$INTERVAL"
done
