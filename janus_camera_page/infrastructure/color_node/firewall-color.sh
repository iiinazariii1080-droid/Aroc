#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Camera-stack firewall rules — Color Node (192.168.1.10)
#
# Preserves Docker-managed chains.  Only touches INPUT chain.
# Deploy: sudo bash /etc/robot/firewall-color.sh
# Persist: sudo netfilter-persistent save
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

IPT="iptables"

# Flush only INPUT (leave FORWARD/OUTPUT/Docker chains alone)
$IPT -F INPUT

# ── Loopback ──
$IPT -A INPUT -i lo -j ACCEPT

# ── Established / related ──
$IPT -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# ── ICMP (ping, MTU discovery) ──
$IPT -A INPUT -p icmp -j ACCEPT

# ── SSH (22) ──
$IPT -A INPUT -p tcp --dport 22 -j ACCEPT

# ── Camera page FastAPI (8900) — LAN + loopback; external via Cloudflare tunnel ──
$IPT -A INPUT -p tcp --dport 8900 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 8900 -s 192.168.1.0/24 -j ACCEPT

# ── Janus WebRTC REST (8088) — LAN only; external via Cloudflare tunnel ──
$IPT -A INPUT -p tcp --dport 8088 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 8088 -s 192.168.1.0/24 -j ACCEPT

# ── Janus WebSocket (8188) — LAN only; external via Cloudflare tunnel ──
$IPT -A INPUT -p tcp --dport 8188 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 8188 -s 192.168.1.0/24 -j ACCEPT

# ── Janus Admin API (7088) — only from LAN ──
$IPT -A INPUT -p tcp --dport 7088 -s 192.168.1.0/24 -j ACCEPT

# ── Janus RTP media (40000-41000 UDP, ICE range — WAN for WebRTC clients) ──
$IPT -A INPUT -p udp --dport 40000:41000 -j ACCEPT

# ── Janus RTP ingest from ffmpeg (5002-5120 UDP, local only) ──
$IPT -A INPUT -p udp --dport 5002:5120 -s 127.0.0.0/8 -j ACCEPT
$IPT -A INPUT -p udp --dport 5002:5120 -s 192.168.1.0/24 -j ACCEPT

# ── TextRoom relay / hook (9000) — LAN + loopback; external via Cloudflare tunnel ──
$IPT -A INPUT -p tcp --dport 9000 -s 127.0.0.0/8    -j ACCEPT
$IPT -A INPUT -p tcp --dport 9000 -s 192.168.1.0/24 -j ACCEPT

# ── Cloudflare tunnel (managed by cloudflared, outbound only) ──
# No inbound rule needed — tunnel is outbound.

# ── Depth node access (allow depth .55 full access on LAN) ──
$IPT -A INPUT -s 192.168.1.55 -j ACCEPT

# ── DHCP client ──
$IPT -A INPUT -p udp --dport 68 -j ACCEPT

# ── Drop everything else ──
$IPT -A INPUT -m limit --limit 30/min --limit-burst 10 -j LOG --log-prefix "FW-DROP: " --log-level 4
$IPT -A INPUT -j DROP

echo "[firewall-color] INPUT rules applied. Run 'sudo netfilter-persistent save' to persist."
