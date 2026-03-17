#!/usr/bin/env bash
set -euo pipefail
NODE="${1:?missing node}"         # ожидаем: %i (например cam-rgb или video4)
[[ "$NODE" = /* ]] || NODE="/dev/$NODE"

for i in {1..80}; do
  if [[ -e "$NODE" ]]; then
    if udevadm info -q property -n "$NODE" | grep -q 'ID_V4L_CAPABILITIES=.*:capture:' \
       && ! fuser "$NODE" >/dev/null 2>&1; then
      # Flush stale v4l2 kernel buffers by capturing a few test frames
      if command -v v4l2-ctl &>/dev/null; then
        v4l2-ctl -d "$NODE" --stream-mmap --stream-count=3 --stream-to=/dev/null 2>/dev/null || true
      fi
      exit 0
    fi
  fi
  sleep 0.25
done

echo "$NODE not capture or busy" >&2
exit 1
