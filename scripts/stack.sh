#!/usr/bin/env bash
# Start/stop SUT (:8080) + mock server (:8081) as local processes and wait for /health.
# Usage: scripts/stack.sh up|down      (SUT_DEFECTS=on scripts/stack.sh up  -> planted defects)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
mkdir -p .stack

down() {
  for f in .stack/mock.pid .stack/sut.pid; do
    pid=$(cat "$f" 2>/dev/null) || continue
    [ -n "$pid" ] && { kill "$pid" 2>/dev/null || echo "pid $pid ($f) already gone"; }
    : >"$f"
  done
}

wait_health() {
  for _ in $(seq 1 50); do
    curl -fsS "$1/health" >/dev/null 2>&1 && return 0
    sleep 0.2
  done
  echo "ENVIRONMENT FAILURE: $1/health not ready in 10s (see .stack/*.log)" >&2
  return 1
}

case "${1:-}" in
  up)
    down
    "$PY" -m uvicorn mockserver.server:app --port 8081 --log-level warning >.stack/mock.log 2>&1 &
    echo $! >.stack/mock.pid
    ROUTE_API_URL=http://127.0.0.1:8081 SUT_DEFECTS="${SUT_DEFECTS:-off}" \
      "$PY" -m uvicorn sut.app:app --port 8080 --log-level warning >.stack/sut.log 2>&1 &
    echo $! >.stack/sut.pid
    wait_health http://127.0.0.1:8081
    wait_health http://127.0.0.1:8080
    echo "stack up (SUT_DEFECTS=${SUT_DEFECTS:-off})"
    ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
