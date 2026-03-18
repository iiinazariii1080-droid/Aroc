#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Camera-stack firewall rules — Depth Node (192.168.1.55)
#
# No Docker on this node, simpler ruleset.
# Deploy: sudo bash /etc/robot/firewall-depth.sh
# Persist: sudo netfilter-persistent save
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

IPT="iptables"

# Flush INPUT
$IPT -F INPUT

# ── Loopback ──
$IPT -A INPUT -i lo -j ACCEPT

# ── Established / related ──
$IPT -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# ── ICMP ──
$IPT -A INPUT -p icmp -j ACCEPT

# ── SSH (22) — LAN + Tailscale only (matches color node policy) ──
$IPT -A INPUT -p tcp --dport 22 -s 192.168.1.0/24 -j ACCEPT
$IPT -A INPUT -p tcp --dport 22 -i tailscale0     -j ACCEPT

# ── Camera page FastAPI (8900) — not running on depth but allow from LAN ──
$IPT -A INPUT -p tcp --dport 8900 -s 192.168.1.0/24 -j ACCEPT

# ── Janus WebRTC REST (8088) — LAN only ──
$IPT -A INPUT -p tcp --dport 8088 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 8088 -s 192.168.1.0/24 -j ACCEPT

# ── Janus WebSocket (8188) — LAN only ──
$IPT -A INPUT -p tcp --dport 8188 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 8188 -s 192.168.1.0/24 -j ACCEPT

# ── Janus Admin API (7088) — only from LAN ──
$IPT -A INPUT -p tcp --dport 7088 -s 192.168.1.0/24 -j ACCEPT

# ── Janus RTP media (40000-41000 UDP, ICE range) ──
$IPT -A INPUT -p udp --dport 40000:41000 -j ACCEPT

# ── Janus RTP ingest from ffmpeg (5002-5120 UDP, local only) ──
$IPT -A INPUT -p udp --dport 5002:5120 -s 127.0.0.0/8 -j ACCEPT

# ── RealSense depth API (8000) — from LAN only ──
$IPT -A INPUT -p tcp --dport 8000 -s 192.168.1.0/24 -j ACCEPT

# ── Tailscale — allow all from tailscale0 ──
$IPT -A INPUT -i tailscale0 -j ACCEPT

# ── Color node (gateway) full access ──
$IPT -A INPUT -s 192.168.1.10 -j ACCEPT

# ── DHCP client ──
$IPT -A INPUT -p udp --dport 68 -j ACCEPT

# ── Drop everything else ──
$IPT -A INPUT -m limit --limit 30/min --limit-burst 10 -j LOG --log-prefix "FW-DROP: " --log-level 4
$IPT -A INPUT -j DROP

echo "[firewall-depth] INPUT rules applied. Run 'sudo netfilter-persistent save' to persist."
