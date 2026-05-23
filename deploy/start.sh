#!/bin/sh
set -e

# Background scheduler: writes to /app/data/<topic>/ (shared with nginx /data/).
python -m agent serve &
agent_pid=$!

cleanup() {
  kill "$agent_pid" 2>/dev/null || true
}

trap cleanup TERM INT

# Foreground web server for App Platform health checks and public traffic.
exec nginx -g "daemon off;" -c /app/deploy/nginx.conf
