#!/usr/bin/env bash
# QoS / traffic classification for camera stack — Color node (P2.7)
# Prioritizes RTP media (UDP 5002-5120, 40000-41000) and Janus signaling
# Uses fq_codel for low-latency fair queuing + DSCP marking for RTP
#
# Deploy: sudo bash /opt/robot/qos-media.sh
# Persist: cron @reboot or systemd ExecStartPost

set -euo pipefail

IFACE_LAN="br0"         # LAN bridge (to depth node)
IFACE_WAN="wlan0"       # Uplink (to internet)

setup_qos() {
    local iface="$1"
    local bw="$2"  # bandwidth in kbit

    # Remove existing qdisc (ignore errors)
    tc qdisc del dev "$iface" root 2>/dev/null || true

    # Root: HTB with default class 30 (best-effort)
    tc qdisc add dev "$iface" root handle 1: htb default 30

    # Main rate limiter
    tc class add dev "$iface" parent 1: classid 1:1 htb rate "${bw}kbit" burst 15k

    # Class 10: RTP media (high priority, 70% bandwidth)
    tc class add dev "$iface" parent 1:1 classid 1:10 htb rate $((bw * 70 / 100))kbit ceil "${bw}kbit" burst 15k prio 1
    # Class 20: Signaling (Janus REST/WS, TURN TCP — 20%)
    tc class add dev "$iface" parent 1:1 classid 1:20 htb rate $((bw * 20 / 100))kbit ceil "${bw}kbit" burst 10k prio 2
    # Class 30: Best-effort (everything else — 10%)
    tc class add dev "$iface" parent 1:1 classid 1:30 htb rate $((bw * 10 / 100))kbit ceil "${bw}kbit" burst 10k prio 3

    # Leaf qdiscs: fq_codel for each class
    tc qdisc add dev "$iface" parent 1:10 handle 10: fq_codel
    tc qdisc add dev "$iface" parent 1:20 handle 20: fq_codel
    tc qdisc add dev "$iface" parent 1:30 handle 30: fq_codel

    # Filters: RTP media → class 10
    # UDP ports 5002-5120 (RTP ingest from ffmpeg/realsense-mux)
    tc filter add dev "$iface" parent 1: protocol ip prio 1 u32 \
        match ip protocol 17 0xff \
        match ip dport 5002 0xfff0 \
        flowid 1:10
    # UDP ports 40000-41000 (Janus RTP relay range)
    tc filter add dev "$iface" parent 1: protocol ip prio 1 u32 \
        match ip protocol 17 0xff \
        match ip dport 40000 0xfc00 \
        flowid 1:10

    # Filters: Signaling → class 20
    # TCP port 8088 (Janus REST), 8188 (Janus WS), 8900 (FastAPI)
    for port in 8088 8188 8900; do
        tc filter add dev "$iface" parent 1: protocol ip prio 2 u32 \
            match ip protocol 6 0xff \
            match ip dport "$port" 0xffff \
            flowid 1:20
    done
}

# DSCP marking for outgoing RTP (EF = 46 = 0x2e → TOS byte = 0xb8)
mark_dscp() {
    # Mark RTP (UDP 5002-5120 and 40000-41000) with DSCP EF
    iptables -t mangle -F POSTROUTING 2>/dev/null || true
    iptables -t mangle -A POSTROUTING -p udp --dport 5002:5120 -j DSCP --set-dscp-class EF
    iptables -t mangle -A POSTROUTING -p udp --dport 40000:41000 -j DSCP --set-dscp-class EF
    # Mark signaling with DSCP AF31 (26)
    for port in 8088 8188 8900; do
        iptables -t mangle -A POSTROUTING -p tcp --dport "$port" -j DSCP --set-dscp-class AF31
    done
}

echo "[QoS] Setting up traffic classes on $IFACE_LAN (100Mbit) ..."
setup_qos "$IFACE_LAN" 100000

echo "[QoS] Setting up traffic classes on $IFACE_WAN (20Mbit) ..."
setup_qos "$IFACE_WAN" 20000

echo "[QoS] Marking DSCP ..."
mark_dscp

echo "[QoS] Done. tc stats:"
tc -s qdisc show dev "$IFACE_LAN" | head -10
tc -s qdisc show dev "$IFACE_WAN" | head -10
