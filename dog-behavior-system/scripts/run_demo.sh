#!/usr/bin/env bash
# Starts the orchestrator, then either replays the canned demo story or runs live detection.
#
#   scripts/run_demo.sh replay [speed]              # e.g. scripts/run_demo.sh replay 1
#   scripts/run_demo.sh live [video_source] [audio_source]
#                                                    # e.g. scripts/run_demo.sh live 0 mic
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
  echo "No .venv found -- run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

MODE="${1:-replay}"
PORT=$("$PYTHON" -c "from app.config import get; print(get('server','port',default=8000))" 2>/dev/null || echo 8000)

echo "Starting orchestrator on :$PORT ..."
"$PYTHON" -m app.orchestrator.server &
SERVER_PID=$!
trap 'echo "stopping server"; kill $SERVER_PID 2>/dev/null || true' EXIT

echo "Waiting for the server to come up..."
for i in $(seq 1 30); do
  if curl -s "http://localhost:$PORT/api/device" > /dev/null 2>&1; then break; fi
  sleep 0.5
done

echo "Dashboard: http://localhost:$PORT/"

if [ "$MODE" = "replay" ]; then
  SPEED="${2:-1}"
  echo "Replaying contracts/fixtures/demo-events.json at ${SPEED}x ..."
  curl -s -X POST "http://localhost:$PORT/api/replay" -H 'Content-Type: application/json' -d "{\"speed\": $SPEED}"
  echo
  echo "Replay is running on the server -- open the dashboard to watch it. Ctrl-C to stop the server."
  wait "$SERVER_PID"
elif [ "$MODE" = "live" ]; then
  VIDEO="${2:-0}"
  AUDIO="${3:-}"
  ARGS=(--video "$VIDEO" --server "http://localhost:$PORT")
  if [ -n "$AUDIO" ]; then
    ARGS+=(--audio "$AUDIO")
  fi
  echo "Starting live detection: python -m services.detection.run ${ARGS[*]}"
  "$PYTHON" -m services.detection.run "${ARGS[@]}"
else
  echo "Unknown mode '$MODE' -- use 'replay [speed]' or 'live [video] [audio]'"
  exit 1
fi
