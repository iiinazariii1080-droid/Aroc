#!/usr/bin/env bash
# ── Camera Stack Infrastructure Audit ───────────────────────────────────────
# Covers checklists 1-12 from the deep audit plan.
# Run on each RPi5 node (color .10 / depth .55) and optionally on the VPS.
#
# Usage:
#   ./scripts/audit_camera_stack.sh              # auto-detect node role
#   ./scripts/audit_camera_stack.sh --role color  # force color node
#   ./scripts/audit_camera_stack.sh --role depth  # force depth node
#   ./scripts/audit_camera_stack.sh --role vps    # coturn VPS audit
# ────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; RST='\033[0m'
PASS=0; WARN=0; FAIL=0; SKIP=0

pass()  { ((PASS++)); printf "${GRN}  [PASS]${RST} %s\n" "$1"; }
warn()  { ((WARN++)); printf "${YEL}  [WARN]${RST} %s\n" "$1"; }
fail()  { ((FAIL++)); printf "${RED}  [FAIL]${RST} %s\n" "$1"; }
skip()  { ((SKIP++)); printf "${CYN}  [SKIP]${RST} %s\n" "$1"; }
header(){ printf "\n${CYN}═══ %s ═══${RST}\n" "$1"; }

# ── Detect or override role ─────────────────────────────────────────────────
ROLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$ROLE" ]]; then
  MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  case "$MY_IP" in
    192.168.1.10) ROLE="color" ;;
    192.168.1.55) ROLE="depth" ;;
    *)
      if systemctl is-active coturn &>/dev/null; then
        ROLE="vps"
      else
        echo "Cannot auto-detect role (IP=$MY_IP). Use --role color|depth|vps"
        exit 1
      fi
      ;;
  esac
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Camera Stack Infrastructure Audit — role: $ROLE"
echo "║  $(date '+%Y-%m-%d %H:%M:%S %Z')  $(hostname)"
echo "╚══════════════════════════════════════════════════════════════╝"

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 1: Hardware & USB Layer
# ════════════════════════════════════════════════════════════════════════════
check_hardware() {
  header "CHECKLIST 1: Hardware & USB"

  # USB speed
  if lsusb -t 2>/dev/null | grep -q "Intel.*RealSense"; then
    local speed
    speed=$(lsusb -t 2>/dev/null | grep -i realsense | head -1 | grep -oP '\d+M' | head -1)
    if [[ "$speed" == "5000M" || "$speed" == "480M" ]]; then
      # 5000M = USB3, 480M = USB2
      if [[ "$speed" == "5000M" ]]; then
        pass "RealSense on USB 3.0 (SuperSpeed $speed)"
      else
        warn "RealSense on USB 2.0 ($speed) — consider USB 3.0 for full bandwidth"
      fi
    else
      warn "RealSense USB speed: $speed (unexpected)"
    fi
  else
    # Alternative check via lsusb
    if lsusb 2>/dev/null | grep -qi "realsense\|8086:0b07\|8086:0b3a\|8086:0b5c"; then
      pass "RealSense camera detected via lsusb"
    else
      fail "No RealSense camera detected"
    fi
  fi

  # dmesg USB errors
  local usb_errors
  usb_errors=$(dmesg 2>/dev/null | grep -ic "overcurrent\|disconnect.*usb\|usb.*error\|device not accepting" || true)
  if [[ "$usb_errors" -eq 0 ]]; then
    pass "No USB errors in dmesg"
  else
    warn "Found $usb_errors USB-related warnings in dmesg"
  fi

  # CPU temperature
  if [[ -f /sys/class/thermal/thermal_zone0/temp ]]; then
    local temp
    temp=$(cat /sys/class/thermal/thermal_zone0/temp)
    local temp_c=$((temp / 1000))
    if [[ $temp_c -lt 70 ]]; then
      pass "CPU temperature: ${temp_c}°C (OK)"
    elif [[ $temp_c -lt 80 ]]; then
      warn "CPU temperature: ${temp_c}°C (elevated)"
    else
      fail "CPU temperature: ${temp_c}°C (CRITICAL)"
    fi
  else
    skip "Thermal zone not available"
  fi

  # Hardware watchdog
  if [[ -e /dev/watchdog ]]; then
    pass "Hardware watchdog device exists (/dev/watchdog)"
  else
    warn "No hardware watchdog (/dev/watchdog)"
  fi

  local runtime_wd
  runtime_wd=$(systemctl show -p RuntimeWatchdogUSec 2>/dev/null | cut -d= -f2 || true)
  if [[ -n "$runtime_wd" && "$runtime_wd" != "0" ]]; then
    pass "RuntimeWatchdog: $runtime_wd"
  else
    warn "RuntimeWatchdog not configured in systemd"
  fi

  # Memory
  local mem_avail
  mem_avail=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo 2>/dev/null || echo "?")
  if [[ "$mem_avail" != "?" && "$mem_avail" -gt 500 ]]; then
    pass "Available RAM: ${mem_avail}MB"
  else
    warn "Available RAM: ${mem_avail}MB (low)"
  fi

  # Disk space
  local disk_use
  disk_use=$(df / 2>/dev/null | awk 'NR==2 {print $5}' | tr -d '%')
  if [[ -n "$disk_use" && "$disk_use" -lt 85 ]]; then
    pass "Root disk usage: ${disk_use}%"
  elif [[ -n "$disk_use" && "$disk_use" -lt 95 ]]; then
    warn "Root disk usage: ${disk_use}% (high)"
  else
    fail "Root disk usage: ${disk_use}% (critical)"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 2: Capture & Encoding Pipeline
# ════════════════════════════════════════════════════════════════════════════
check_pipeline() {
  header "CHECKLIST 2: Capture & Encoding Pipeline"

  # ffmpeg processes
  local ffmpeg_count
  ffmpeg_count=$(pgrep -c ffmpeg 2>/dev/null || echo "0")
  if [[ "$ffmpeg_count" -gt 0 ]]; then
    pass "ffmpeg running ($ffmpeg_count processes)"
    # Check for baseline profile
    if ps aux 2>/dev/null | grep ffmpeg | grep -q "baseline"; then
      pass "ffmpeg using H.264 baseline profile"
    else
      warn "Cannot confirm H.264 baseline profile in running ffmpeg"
    fi
    # Check for bitrate cap
    if ps aux 2>/dev/null | grep ffmpeg | grep -q "maxrate"; then
      pass "ffmpeg has bitrate cap (maxrate set)"
    else
      warn "ffmpeg may lack bitrate cap — check systemd unit"
    fi
  else
    warn "No ffmpeg processes running (streams may be stopped)"
  fi

  # FIFO pipes
  for fifo in /run/realsense/color.fifo /run/realsense/depth.fifo; do
    if [[ -p "$fifo" ]]; then
      pass "FIFO exists: $fifo"
    else
      if [[ "$ROLE" == "depth" ]] || [[ "$fifo" == *color* ]]; then
        warn "FIFO missing: $fifo (capture may not be running)"
      fi
    fi
  done

  # Snapshot file
  local snap="/run/cam-rgb/snapshot.jpg"
  if [[ -f "$snap" ]]; then
    local snap_age
    snap_age=$(( $(date +%s) - $(stat -c %Y "$snap" 2>/dev/null || echo 0) ))
    if [[ $snap_age -lt 30 ]]; then
      pass "Snapshot fresh: ${snap_age}s old"
    else
      warn "Snapshot stale: ${snap_age}s old"
    fi
  else
    skip "No snapshot file at $snap"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 3: Janus WebRTC Gateway
# ════════════════════════════════════════════════════════════════════════════
check_janus() {
  header "CHECKLIST 3: Janus WebRTC Gateway"

  # Service status
  if systemctl is-active --quiet janus 2>/dev/null; then
    pass "janus.service active"
  else
    fail "janus.service NOT active"
    return
  fi

  # Janus user
  local janus_user
  janus_user=$(ps -o user= -C janus 2>/dev/null | head -1 | tr -d ' ')
  if [[ "$janus_user" == "janus" ]]; then
    pass "Janus running as user: janus"
  elif [[ -n "$janus_user" ]]; then
    warn "Janus running as user: $janus_user (expected: janus)"
  fi

  # Admin API probe
  local admin_resp
  admin_resp=$(curl -sf --max-time 3 "http://127.0.0.1:7088/admin" \
    -d '{"janus":"server_info","transaction":"audit","admin_secret":"e3ec4ead6a41e9b631c8dbd4c3b4ac0e"}' 2>/dev/null || true)
  if [[ -n "$admin_resp" ]] && echo "$admin_resp" | grep -q '"janus"'; then
    pass "Janus Admin API (7088) responding"
    # Extract version
    local ver
    ver=$(echo "$admin_resp" | grep -oP '"version_string"\s*:\s*"\K[^"]+' || true)
    [[ -n "$ver" ]] && pass "Janus version: $ver"
  else
    warn "Janus Admin API not responding (port 7088)"
  fi

  # REST API
  local rest_resp
  rest_resp=$(curl -sf --max-time 3 "http://127.0.0.1:8088/janus/info" 2>/dev/null || true)
  if [[ -n "$rest_resp" ]] && echo "$rest_resp" | grep -q '"janus"'; then
    pass "Janus REST API (8088) responding"
  else
    warn "Janus REST API not responding (port 8088)"
  fi

  # WebSocket
  if command -v websocat &>/dev/null; then
    if echo '{"janus":"info","transaction":"audit"}' | timeout 3 websocat -1 ws://127.0.0.1:8188 2>/dev/null | grep -q '"janus"'; then
      pass "Janus WebSocket (8188) responding"
    else
      warn "Janus WebSocket (8188) not responding"
    fi
  else
    skip "websocat not installed — cannot test WS 8188"
  fi

  # Mount points via admin API
  if [[ -n "$admin_resp" ]]; then
    for mp_id in 1305 1306; do
      local mp_resp
      mp_resp=$(curl -sf --max-time 3 "http://127.0.0.1:7088/admin" \
        -d "{\"janus\":\"message_plugin\",\"transaction\":\"audit_mp\",\"admin_secret\":\"e3ec4ead6a41e9b631c8dbd4c3b4ac0e\",\"plugin\":\"janus.plugin.streaming\",\"request\":{\"request\":\"info\",\"id\":$mp_id}}" 2>/dev/null || true)
      if echo "$mp_resp" | grep -q '"id"' 2>/dev/null; then
        pass "Mount point $mp_id exists"
        # Check media age
        local age_ms
        age_ms=$(echo "$mp_resp" | grep -oP '"age_ms"\s*:\s*\K\d+' | head -1 || true)
        if [[ -n "$age_ms" && "$age_ms" -lt 10000 ]]; then
          pass "Mount $mp_id media alive (age: ${age_ms}ms)"
        elif [[ -n "$age_ms" ]]; then
          warn "Mount $mp_id media stale (age: ${age_ms}ms)"
        fi
      else
        if [[ "$ROLE" == "color" && "$mp_id" == "1306" ]]; then
          skip "Mount point 1306 (depth) — expected on depth node only"
        else
          warn "Mount point $mp_id not found"
        fi
      fi
    done
  fi

  # LimitNOFILE
  local nofile
  nofile=$(grep -r "LimitNOFILE" /etc/systemd/system/janus.service 2>/dev/null | grep -oP '\d+' || true)
  if [[ -n "$nofile" && "$nofile" -ge 65535 ]]; then
    pass "Janus LimitNOFILE=$nofile"
  elif [[ -n "$nofile" ]]; then
    warn "Janus LimitNOFILE=$nofile (recommended: 65535)"
  else
    skip "Cannot read LimitNOFILE from janus.service"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 4: NAT Traversal & TURN
# ════════════════════════════════════════════════════════════════════════════
check_turn() {
  header "CHECKLIST 4: NAT Traversal & TURN"

  local turn_host="82.165.177.194"

  # TURN connectivity (UDP)
  if timeout 3 bash -c "echo -n '' > /dev/udp/$turn_host/3478" 2>/dev/null; then
    pass "TURN UDP 3478 reachable"
  else
    warn "TURN UDP 3478 unreachable (may be blocked or host down)"
  fi

  # TURN connectivity (TCP)
  if timeout 3 bash -c "echo -n '' > /dev/tcp/$turn_host/3478" 2>/dev/null; then
    pass "TURN TCP 3478 reachable"
  else
    warn "TURN TCP 3478 unreachable"
  fi

  # TURNS/TLS
  if timeout 3 bash -c "echo -n '' > /dev/tcp/$turn_host/443" 2>/dev/null; then
    pass "TURNS TLS 443 reachable"
  else
    warn "TURNS TLS 443 unreachable"
  fi

  # Client-config endpoint
  local cc_resp
  cc_resp=$(curl -sf --max-time 5 "http://127.0.0.1:8900/client-config" 2>/dev/null || true)
  if echo "$cc_resp" | grep -q '"iceServers"' 2>/dev/null; then
    pass "/client-config returns ICE servers"
    # Check for ephemeral credentials
    if echo "$cc_resp" | grep -q '"credentialType"' 2>/dev/null; then
      pass "TURN credentials present in client-config"
    else
      warn "No TURN credentials in client-config (check TURN_SHARED_SECRET)"
    fi
    # Check transport policy
    local policy
    policy=$(echo "$cc_resp" | grep -oP '"iceTransportPolicy"\s*:\s*"\K[^"]+' || true)
    if [[ "$ROLE" == "depth" && "$policy" == "relay" ]]; then
      pass "ICE transport policy: relay (correct for depth/double NAT)"
    elif [[ "$ROLE" == "color" ]]; then
      pass "ICE transport policy: $policy"
    fi
  else
    warn "/client-config not responding on :8900"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 5: Firewall & QoS
# ════════════════════════════════════════════════════════════════════════════
check_firewall_qos() {
  header "CHECKLIST 5: Firewall & QoS"

  # iptables INPUT policy
  local input_policy
  input_policy=$(iptables -L INPUT -n 2>/dev/null | head -1 | grep -oP 'policy \K\w+' || true)
  if [[ "$input_policy" == "DROP" ]]; then
    pass "INPUT policy: DROP (secure)"
  elif [[ "$input_policy" == "ACCEPT" ]]; then
    warn "INPUT policy: ACCEPT (should be DROP)"
  else
    skip "Cannot read iptables (need root?)"
  fi

  # Check if Janus admin port restricted
  if iptables -L INPUT -n 2>/dev/null | grep -q "7088"; then
    pass "Port 7088 (Admin API) has firewall rules"
  else
    warn "Port 7088 (Admin API) not restricted by firewall"
  fi

  # IP forwarding (color node = gateway)
  if [[ "$ROLE" == "color" ]]; then
    local ip_fwd
    ip_fwd=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo "?")
    if [[ "$ip_fwd" == "1" ]]; then
      pass "ip_forward=1 (gateway role enabled)"
    else
      fail "ip_forward=$ip_fwd (should be 1 for gateway role)"
    fi
  fi

  # QoS qdiscs
  local htb_count
  htb_count=$(tc qdisc show 2>/dev/null | grep -c "htb" || echo "0")
  if [[ "$htb_count" -gt 0 ]]; then
    pass "HTB qdisc active ($htb_count instances)"
  else
    warn "No HTB qdisc — QoS may not be configured"
  fi

  local fq_count
  fq_count=$(tc qdisc show 2>/dev/null | grep -c "fq_codel" || echo "0")
  if [[ "$fq_count" -gt 0 ]]; then
    pass "fq_codel active ($fq_count leaf qdiscs)"
  else
    warn "No fq_codel — QoS leaf qdiscs may not be configured"
  fi

  # iptables persistence
  if dpkg -l 2>/dev/null | grep -q iptables-persistent; then
    pass "iptables-persistent installed"
  elif systemctl is-enabled firewall-camera 2>/dev/null | grep -q "enabled"; then
    pass "firewall-camera.service enabled at boot"
  else
    warn "No iptables persistence mechanism detected"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 6: FastAPI Application Layer
# ════════════════════════════════════════════════════════════════════════════
check_fastapi() {
  header "CHECKLIST 6: FastAPI Application"

  # Camera page service
  local svc_name="janus-camera-page"
  [[ "$ROLE" == "depth" ]] && svc_name="janus-camera-page"

  if systemctl is-active --quiet "$svc_name" 2>/dev/null; then
    pass "$svc_name.service active"
  else
    # Try alternate names
    for alt in camera-page janus_camera_page; do
      if systemctl is-active --quiet "$alt" 2>/dev/null; then
        pass "$alt.service active"
        svc_name="$alt"
        break
      fi
    done
  fi

  # Healthz
  local hz
  hz=$(curl -sf --max-time 5 "http://127.0.0.1:8900/healthz" 2>/dev/null || true)
  if [[ -n "$hz" ]]; then
    pass "/healthz responding"
    if echo "$hz" | grep -q '"ok"' 2>/dev/null || echo "$hz" | grep -q '"healthy"' 2>/dev/null; then
      pass "/healthz reports healthy"
    else
      warn "/healthz response: $(echo "$hz" | head -c 200)"
    fi
  else
    fail "FastAPI :8900 /healthz not responding"
  fi

  # Prometheus metrics
  local metrics
  metrics=$(curl -sf --max-time 5 "http://127.0.0.1:8900/metrics" 2>/dev/null | head -5 || true)
  if [[ -n "$metrics" ]] && echo "$metrics" | grep -q "camstack\|http_request"; then
    pass "/metrics endpoint available"
  else
    skip "/metrics endpoint not available"
  fi

  # Security headers
  local headers
  headers=$(curl -sI --max-time 5 "http://127.0.0.1:8900/healthz" 2>/dev/null || true)
  if echo "$headers" | grep -qi "x-content-type-options"; then
    pass "X-Content-Type-Options header set"
  else
    warn "X-Content-Type-Options header missing"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 7: FDIR Recovery System
# ════════════════════════════════════════════════════════════════════════════
check_fdir() {
  header "CHECKLIST 7: FDIR Recovery System"

  # FDIR state file
  local state_file="/run/camera/fdir_ladder.json"
  if [[ -f "$state_file" ]]; then
    pass "FDIR state file exists: $state_file"
    local level
    level=$(grep -oP '"level"\s*:\s*\K\d+' "$state_file" 2>/dev/null || echo "?")
    pass "Current FDIR ladder level: $level"
  else
    skip "No FDIR state file (may not have been triggered)"
  fi

  # Reboot counter
  local reboot_file="/var/lib/camera-fdir/reboot_count"
  if [[ -f "$reboot_file" ]]; then
    local count
    count=$(cat "$reboot_file" 2>/dev/null || echo "?")
    if [[ "$count" -le 2 ]] 2>/dev/null; then
      pass "FDIR reboot count: $count (within circuit breaker limit)"
    else
      warn "FDIR reboot count: $count (approaching/exceeding limit)"
    fi
  else
    pass "No reboot counter file (no FDIR reboots occurred)"
  fi

  # FDIR event log
  local fdir_log="/var/log/camera-fdir/fdir.jsonl"
  if [[ -f "$fdir_log" ]]; then
    local log_size
    log_size=$(stat -c %s "$fdir_log" 2>/dev/null || echo "0")
    local log_size_mb=$((log_size / 1024 / 1024))
    if [[ $log_size_mb -lt 5 ]]; then
      pass "FDIR log size: ${log_size_mb}MB (within rotation limit)"
    else
      warn "FDIR log size: ${log_size_mb}MB (should rotate at 5MB)"
    fi
    local event_count
    event_count=$(wc -l < "$fdir_log" 2>/dev/null || echo "0")
    pass "FDIR events logged: $event_count"
  else
    skip "No FDIR event log yet"
  fi

  # FDIR API
  local fdir_api
  fdir_api=$(curl -sf --max-time 5 "http://127.0.0.1:8900/api/v1/$([ "$ROLE" == "color" ] && echo "color_camera" || echo "depth_camera")/fdir/events?n=5" 2>/dev/null || true)
  if [[ -n "$fdir_api" ]]; then
    pass "FDIR events API responding"
  else
    skip "FDIR events API not available"
  fi

  # System mode
  local mode_resp
  mode_resp=$(curl -sf --max-time 5 "http://127.0.0.1:8900/api/v1/$([ "$ROLE" == "color" ] && echo "color_camera" || echo "depth_camera")/system/mode" 2>/dev/null || true)
  if echo "$mode_resp" | grep -qi "NOMINAL" 2>/dev/null; then
    pass "System mode: NOMINAL"
  elif [[ -n "$mode_resp" ]]; then
    warn "System mode: $(echo "$mode_resp" | head -c 100)"
  else
    skip "System mode API not available"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 9: Systemd Orchestration
# ════════════════════════════════════════════════════════════════════════════
check_systemd() {
  header "CHECKLIST 9: Systemd Orchestration"

  local services=()
  if [[ "$ROLE" == "color" ]]; then
    services=(janus janus-camera-page)
  elif [[ "$ROLE" == "depth" ]]; then
    services=(janus janus-camera-page rtp-rgb rtp-depth)
  fi

  for svc in "${services[@]}"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      pass "$svc.service: active"
      # Check restart count
      local restarts
      restarts=$(systemctl show "$svc" -p NRestarts 2>/dev/null | cut -d= -f2 || true)
      if [[ -n "$restarts" && "$restarts" -gt 0 ]]; then
        warn "$svc has restarted $restarts times since last daemon-reload"
      fi
    elif systemctl is-enabled --quiet "$svc" 2>/dev/null; then
      warn "$svc.service: enabled but not active"
    else
      # Try with underscores
      local alt="${svc//-/_}"
      if systemctl is-active --quiet "$alt" 2>/dev/null; then
        pass "$alt.service: active"
      else
        warn "$svc.service: not found or not enabled"
      fi
    fi
  done

  # journald persistence
  if [[ -d /var/log/journal ]]; then
    pass "journald persistent storage enabled"
    local journal_size
    journal_size=$(du -sm /var/log/journal 2>/dev/null | awk '{print $1}' || echo "?")
    pass "Journal size: ${journal_size}MB"
  else
    warn "journald using volatile storage only"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 11: Network & Connectivity
# ════════════════════════════════════════════════════════════════════════════
check_network() {
  header "CHECKLIST 11: Network & Connectivity"

  # Ping between nodes
  if [[ "$ROLE" == "color" ]]; then
    if ping -c 2 -W 2 192.168.1.55 &>/dev/null; then
      pass "Ping to depth node (192.168.1.55): OK"
    else
      fail "Cannot ping depth node (192.168.1.55)"
    fi
    # Bridge interface
    if ip link show br0 &>/dev/null; then
      pass "Bridge interface br0 exists"
    else
      warn "No br0 bridge interface"
    fi
  elif [[ "$ROLE" == "depth" ]]; then
    if ping -c 2 -W 2 192.168.1.10 &>/dev/null; then
      pass "Ping to color node (192.168.1.10): OK"
    else
      fail "Cannot ping color node (192.168.1.10)"
    fi
    # Default gateway
    local gw
    gw=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)
    if [[ "$gw" == "192.168.1.10" || "$gw" == "192.168.1.1" ]]; then
      pass "Default gateway: $gw"
    else
      warn "Default gateway: $gw (expected 192.168.1.10 or .1)"
    fi
  fi

  # Internet connectivity
  if curl -sf --max-time 5 "https://ifconfig.me" &>/dev/null; then
    local pub_ip
    pub_ip=$(curl -sf --max-time 5 "https://ifconfig.me" 2>/dev/null || true)
    pass "Internet access OK (public IP: $pub_ip)"
  else
    if [[ "$ROLE" == "depth" ]]; then
      skip "Depth node has no internet — expected (isolated router)"
    else
      warn "No internet access"
    fi
  fi

  # NTP sync
  if timedatectl show 2>/dev/null | grep -q "NTPSynchronized=yes"; then
    pass "NTP synchronized"
  elif ntpstat &>/dev/null; then
    pass "NTP synchronized (ntpstat)"
  else
    warn "NTP may not be synchronized"
  fi

  # DNS
  if host -W 3 google.com &>/dev/null || nslookup google.com &>/dev/null 2>&1; then
    pass "DNS resolution working"
  else
    if [[ "$ROLE" == "depth" ]]; then
      skip "DNS may not work on isolated depth node"
    else
      warn "DNS resolution failed"
    fi
  fi

  # Tailscale (depth node OOB)
  if [[ "$ROLE" == "depth" ]]; then
    if ip link show tailscale0 &>/dev/null; then
      pass "Tailscale interface active"
    else
      warn "No Tailscale interface (OOB access unavailable)"
    fi
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# CHECKLIST 12: Observability & SLOs
# ════════════════════════════════════════════════════════════════════════════
check_observability() {
  header "CHECKLIST 12: Observability & SLOs"

  # Prometheus metrics available
  local metrics
  metrics=$(curl -sf --max-time 5 "http://127.0.0.1:8900/metrics" 2>/dev/null || true)
  if [[ -n "$metrics" ]]; then
    for metric in camstack_fdir_events_total camstack_watchdog_checks_total camstack_mode; do
      if echo "$metrics" | grep -q "$metric"; then
        pass "Metric present: $metric"
      else
        skip "Metric missing: $metric"
      fi
    done
  else
    skip "Prometheus /metrics not available"
  fi

  # Uptime
  local uptime_s
  uptime_s=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo "0")
  local uptime_h=$((uptime_s / 3600))
  pass "System uptime: ${uptime_h}h"
}

# ════════════════════════════════════════════════════════════════════════════
# VPS coturn audit (CHECKLIST 4 extended)
# ════════════════════════════════════════════════════════════════════════════
check_coturn_vps() {
  header "VPS: coturn Configuration Audit"

  if ! systemctl is-active --quiet coturn 2>/dev/null; then
    fail "coturn.service NOT active"
    return
  fi
  pass "coturn.service active"

  local conf="/etc/turnserver.conf"
  if [[ ! -f "$conf" ]]; then
    conf=$(find /etc -name "turnserver.conf" 2>/dev/null | head -1)
  fi

  if [[ -z "$conf" || ! -f "$conf" ]]; then
    fail "Cannot find turnserver.conf"
    return
  fi

  # Security settings
  if grep -q "^use-auth-secret" "$conf" 2>/dev/null; then
    pass "use-auth-secret enabled (REST API mode)"
  else
    fail "use-auth-secret NOT set — using static passwords"
  fi

  if grep -q "^no-multicast-peers" "$conf" 2>/dev/null; then
    pass "no-multicast-peers set"
  else
    warn "no-multicast-peers NOT set"
  fi

  if grep -q "^no-loopback-peers" "$conf" 2>/dev/null; then
    pass "no-loopback-peers set"
  else
    warn "no-loopback-peers NOT set"
  fi

  # Denied peer prefixes
  local denied
  denied=$(grep -c "^denied-peer-ip" "$conf" 2>/dev/null || echo "0")
  if [[ "$denied" -gt 0 ]]; then
    pass "denied-peer-ip rules: $denied (RFC1918 blocked)"
  else
    warn "No denied-peer-ip rules — private relay possible"
  fi

  # Stale nonce
  if grep -q "^stale-nonce" "$conf" 2>/dev/null; then
    local nonce_sec
    nonce_sec=$(grep "^stale-nonce" "$conf" | grep -oP '\d+' || echo "?")
    pass "stale-nonce: ${nonce_sec}s"
  else
    warn "stale-nonce not configured"
  fi

  # User quota
  if grep -q "^user-quota" "$conf" 2>/dev/null; then
    pass "user-quota set: $(grep '^user-quota' "$conf" | head -1)"
  else
    warn "user-quota not set"
  fi

  # TLS cert
  local cert_file
  cert_file=$(grep "^cert=" "$conf" 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)
  if [[ -n "$cert_file" && -f "$cert_file" ]]; then
    local expiry
    expiry=$(openssl x509 -enddate -noout -in "$cert_file" 2>/dev/null | cut -d= -f2 || true)
    if [[ -n "$expiry" ]]; then
      local exp_epoch
      exp_epoch=$(date -d "$expiry" +%s 2>/dev/null || echo "0")
      local now_epoch
      now_epoch=$(date +%s)
      local days_left=$(( (exp_epoch - now_epoch) / 86400 ))
      if [[ $days_left -gt 30 ]]; then
        pass "TLS cert valid for ${days_left} days ($expiry)"
      elif [[ $days_left -gt 0 ]]; then
        warn "TLS cert expiring in ${days_left} days ($expiry)"
      else
        fail "TLS cert EXPIRED ($expiry)"
      fi
    fi
  elif [[ -n "$cert_file" ]]; then
    fail "TLS cert file not found: $cert_file"
  else
    warn "No TLS cert configured in turnserver.conf"
  fi

  # Relay port range
  local min_port max_port
  min_port=$(grep "^min-port" "$conf" 2>/dev/null | grep -oP '\d+' || echo "?")
  max_port=$(grep "^max-port" "$conf" 2>/dev/null | grep -oP '\d+' || echo "?")
  pass "Relay port range: $min_port-$max_port"

  # Listening ports
  for port in 3478 443 5349; do
    if ss -tlnp 2>/dev/null | grep -q ":$port "; then
      pass "Listening on TCP port $port"
    else
      warn "Not listening on TCP port $port"
    fi
  done
}

# ════════════════════════════════════════════════════════════════════════════
# Run checklists based on role
# ════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "vps" ]]; then
  check_coturn_vps
else
  check_hardware
  check_pipeline
  check_janus
  check_turn
  check_firewall_qos
  check_fastapi
  check_fdir
  check_systemd
  check_network
  check_observability
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  Results: ${GRN}PASS=%d${RST}  ${YEL}WARN=%d${RST}  ${RED}FAIL=%d${RST}  ${CYN}SKIP=%d${RST}\n" $PASS $WARN $FAIL $SKIP
echo "╚══════════════════════════════════════════════════════════════╝"

if [[ $FAIL -gt 0 ]]; then
  exit 2
elif [[ $WARN -gt 0 ]]; then
  exit 1
fi
exit 0
