#!/bin/sh
set -eu

if [ $# -eq 0 ]; then
  echo "Error: no command specified" >&2
  exit 1
fi

# Ensure /app/data directory exists and has correct permissions
# This handles cases where volume mounts create the directory with wrong permissions
mkdir -p /app/data
chown -R appuser:appuser /app/data
chmod -R 755 /app/data

# Switch to appuser and execute the main command
exec su-exec appuser "$@"
