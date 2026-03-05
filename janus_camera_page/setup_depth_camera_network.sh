#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# setup_depth_camera_network.sh
#
# Run on 192.168.1.10 (color_camera host) to enable IP forwarding and
# NAT masquerade so the depth camera (192.168.1.55) on the isolated
# WiFi router can reach the internet (TURN server, etc.).
#
# Prerequisites:
#   - 192.168.1.10 has two network interfaces:
#       ISOLATED_IFACE  – connected to isolated router (192.168.1.x)
#       CORP_IFACE      – connected to corporate router (internet)
#   - 192.168.1.55 reachable from 192.168.1.10 on ISOLATED_IFACE
#
# Usage:
#   sudo bash setup_depth_camera_network.sh [CORP_IFACE]
#
#   CORP_IFACE defaults to the interface of the default route.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

DEPTH_IP="192.168.1.55"

# Auto-detect corporate interface (the one with the default route).
CORP_IFACE="${1:-$(ip route show default | awk '{print $5; exit}')}"
if [[ -z "$CORP_IFACE" ]]; then
    echo "ERROR: Could not detect corporate WiFi interface. Pass it as \$1."
    exit 1
fi
echo "Corporate interface: $CORP_IFACE"

# ── 1. Enable IP forwarding ──────────────────────────────────────────
echo "Enabling IP forwarding…"
sysctl -w net.ipv4.ip_forward=1
if ! grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.d/99-depth-forward.conf 2>/dev/null; then
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-depth-forward.conf
    echo "  Persisted to /etc/sysctl.d/99-depth-forward.conf"
fi

# ── 2. NAT masquerade for depth camera traffic ───────────────────────
echo "Adding iptables NAT masquerade for $DEPTH_IP → $CORP_IFACE…"

# Avoid duplicate rules
if ! iptables -t nat -C POSTROUTING -s "$DEPTH_IP" -o "$CORP_IFACE" -j MASQUERADE 2>/dev/null; then
    iptables -t nat -A POSTROUTING -s "$DEPTH_IP" -o "$CORP_IFACE" -j MASQUERADE
fi

if ! iptables -C FORWARD -s "$DEPTH_IP" -j ACCEPT 2>/dev/null; then
    iptables -A FORWARD -s "$DEPTH_IP" -j ACCEPT
fi

if ! iptables -C FORWARD -d "$DEPTH_IP" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
    iptables -A FORWARD -d "$DEPTH_IP" -m state --state RELATED,ESTABLISHED -j ACCEPT
fi

echo "Done. iptables rules applied."

# ── 3. Persist iptables across reboots (optional) ────────────────────
if command -v netfilter-persistent &>/dev/null; then
    echo "Saving iptables rules via netfilter-persistent…"
    netfilter-persistent save
elif command -v iptables-save &>/dev/null; then
    echo "Saving iptables rules to /etc/iptables/rules.v4…"
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4
fi

echo ""
echo "── Next steps (on 192.168.1.55) ──"
echo "  sudo ip route replace default via 192.168.1.10"
echo "  # Make permanent: add to /etc/network/interfaces or netplan"
echo ""
echo "── Verify ──"
echo "  ssh 192.168.1.55 'ping -c 2 82.165.177.194'   # TURN server"
echo ""
echo "── Environment for depth camera service on 192.168.1.55 ──"
echo "  ICE_POLICY=relay"
echo ""
