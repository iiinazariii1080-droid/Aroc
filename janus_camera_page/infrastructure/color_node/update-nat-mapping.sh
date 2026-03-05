#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Auto-update nat_1_1_mapping in Janus config when public IP changes.
# Runs via cron: */15 * * * * /etc/robot/update-nat-mapping.sh
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

JANUS_CFG="/opt/janus/etc/janus/janus.jcfg"
IP_CACHE="/var/tmp/janus-public-ip.cache"
LOG_TAG="nat-mapping"

# Get current public IP (try multiple services)
NEW_IP=$(curl -sf --max-time 5 https://ifconfig.me 2>/dev/null \
      || curl -sf --max-time 5 https://api.ipify.org 2>/dev/null \
      || curl -sf --max-time 5 https://checkip.amazonaws.com 2>/dev/null \
      || echo "")

if [[ -z "$NEW_IP" ]]; then
    logger -t "$LOG_TAG" "WARN: could not determine public IP"
    exit 0
fi

# Check cached IP
OLD_IP=""
[[ -f "$IP_CACHE" ]] && OLD_IP=$(cat "$IP_CACHE")

if [[ "$NEW_IP" == "$OLD_IP" ]]; then
    exit 0  # no change
fi

# Update Janus config
if grep -q "nat_1_1_mapping" "$JANUS_CFG"; then
    sed -i "s/nat_1_1_mapping = \".*\"/nat_1_1_mapping = \"$NEW_IP\"/" "$JANUS_CFG"
else
    # Insert into nat block before closing brace
    sed -i "/^nat: {/a \\  nat_1_1_mapping = \"$NEW_IP\"" "$JANUS_CFG"
fi

echo "$NEW_IP" > "$IP_CACHE"
logger -t "$LOG_TAG" "Updated nat_1_1_mapping: $OLD_IP -> $NEW_IP, restarting Janus"
systemctl restart janus.service
