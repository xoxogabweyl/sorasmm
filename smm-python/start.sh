#!/usr/bin/env sh
set -eu

PORT_VALUE="${PORT:-8080}"
case "$PORT_VALUE" in
  ''|*[!0-9]*)
    PORT_VALUE="8080"
    ;;
esac

exec gunicorn run:app --bind "0.0.0.0:${PORT_VALUE}" --workers 2
