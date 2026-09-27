#!/usr/bin/env bash
# Starts Paw Patrol (orchestrator + dashboard), then live detection.
#
#   scripts/run_demo.sh live [video] [audio]   # from scratch: only what happens from now on
#   scripts/run_demo.sh demo [video] [audio]   # a ~3 h sample story first, then live events on top
#   scripts/run_demo.sh replay [speed]         # canned 60-second event story, no video
#
#   video: webcam index (default 0), a file path, or an rtsp/http URL
#   audio: "mic", or a file (a video file's own sound is picked up automatically)
#   A video file plays once in live mode (e.g. a recorded session) and loops in demo mode.
#   e.g.   scripts/run_demo.sh live 0 mic
#          scripts/run_demo.sh demo data/demo_videos/running-1.mp4
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
  echo "No .venv found -- run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

MODE="${1:-live}"
case "$MODE" in
  live|replay) export PAWPATROL_MODE=live ;;
  demo) export PAWPATROL_MODE=demo ;;
  *) echo "Unknown mode '$MODE' -- use: live | demo | replay"; exit 1 ;;
esac
PORT=$("$PYTHON" -c "from app.config import get; print(get('server','port',default=8000))" 2>/dev/null || echo 8000)

echo "Starting Paw Patrol ($PAWPATROL_MODE mode) on :$PORT ..."
"$PYTHON" -m app.orchestrator.server &
SERVER_PID=$!
trap 'echo "stopping server"; kill $SERVER_PID 2>/dev/null || true' EXIT

for i in $(seq 1 60); do
  if curl -s "http://localhost:$PORT/api/device" > /dev/null 2>&1; then break; fi
  sleep 0.5
done
echo "Dashboard: http://localhost:$PORT/"

if [ "$MODE" = "replay" ]; then
  SPEED="${2:-1}"
  curl -s -X POST "http://localhost:$PORT/api/replay" -H 'Content-Type: application/json' -d "{\"speed\": $SPEED}" > /dev/null
  echo "Replaying the canned story at ${SPEED}x -- Ctrl-C to stop."
  wait "$SERVER_PID"
else
  VIDEO="${2:-0}"
  AUDIO="${3:-}"
  ARGS=(--video "$VIDEO" --server "http://localhost:$PORT")
  # a video file plays once in live mode (a recorded session); demo mode loops it
  if [ "$MODE" = "live" ]; then ARGS+=(--no-loop); else ARGS+=(--loop); fi
  if [ -n "$AUDIO" ]; then
    ARGS+=(--audio "$AUDIO")
  fi
  echo "Live detection: python -m services.detection.run ${ARGS[*]}"
  "$PYTHON" -m services.detection.run "${ARGS[@]}" || true
  echo "Video finished -- the dashboard stays up at http://localhost:$PORT/ (Ctrl-C to stop)."
  wait "$SERVER_PID"
fi
