#!/bin/sh
set -e

# Background scheduler: writes events/images to MongoDB; run reports to /app/data/<topic>/.
python -m agent serve &
agent_pid=$!

# REST API for Angular (events + poster images from MongoDB).
python -m agent api --host 127.0.0.1 --port 8765 &
api_pid=$!

cleanup() {
  kill "$agent_pid" 2>/dev/null || true
  kill "$api_pid" 2>/dev/null || true
}

trap cleanup TERM INT

# Foreground web server for App Platform health checks and public traffic.
exec nginx -g "daemon off;" -c /app/deploy/nginx.conf
