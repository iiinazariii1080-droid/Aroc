#!/bin/bash
set -e

# Ensure /app/data directory exists and has correct permissions
# This handles cases where volume mounts create the directory with wrong permissions
mkdir -p /app/data
chown -R appuser:appuser /app/data
chmod -R 755 /app/data

# Switch to appuser and execute the main command
exec gosu appuser "$@"
