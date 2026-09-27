#!/usr/bin/env bash
# Starts Paw Patrol (orchestrator + dashboard), then the detectors.
#
#   scripts/run_demo.sh live [video] [audio] [options]   # from scratch: only what happens from now on
#   scripts/run_demo.sh demo [video] [audio] [options]   # a ~3 h sample story first, then live events on top
#   scripts/run_demo.sh playback <recording>             # a processed recording, played back in sync
#   scripts/run_demo.sh replay [speed]                   # canned 60-second event story, no video
#
#   video: webcam index (default 0), a file path, or an rtsp/http URL
#   audio: "mic", or a file (a video file's own sound is picked up automatically)
#   options:
#     --collar PATH         collar session folder, or the collar's sessions dir (follows current.txt);
#                           the gyro drives gait, the collar mic says "our dog?", the collar LED mirrors alerts
#     --collar-offset S     seconds added to collar times (only if its receiver runs on another machine)
#     --record [NAME]       also save camera video + mic audio to data/recordings/<NAME>/ for later syncing
#   A video file plays once in live mode (e.g. a recorded session) and loops in demo mode.
#
#   e.g.   scripts/run_demo.sh live 0 mic --collar ../paw-patrol/sessions --record dog1
#          scripts/run_demo.sh playback data/recordings/dog1
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
  echo "No .venv found -- run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

MODE="${1:-live}"
[ $# -gt 0 ] && shift
POS=()
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --collar) EXTRA+=(--collar "$2"); export PAWPATROL_COLLAR="$2"; shift 2 ;;
    --collar-offset) EXTRA+=(--collar-offset "$2"); shift 2 ;;
    --record)
      if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then EXTRA+=(--record "$2"); shift 2; else EXTRA+=(--record); shift; fi ;;
    *) POS+=("$1"); shift ;;
  esac
done

case "$MODE" in
  live|replay|playback) export PAWPATROL_MODE=live ;;
  demo) export PAWPATROL_MODE=demo ;;
  *) echo "Unknown mode '$MODE' -- use: live | demo | playback | replay"; exit 1 ;;
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
  SPEED="${POS[0]:-1}"
  curl -s -X POST "http://localhost:$PORT/api/replay" -H 'Content-Type: application/json' -d "{\"speed\": $SPEED}" > /dev/null
  echo "Replaying the canned story at ${SPEED}x -- Ctrl-C to stop."
  wait "$SERVER_PID"
elif [ "$MODE" = "playback" ]; then
  REC="${POS[0]:?usage: scripts/run_demo.sh playback data/recordings/<name>}"
  "$PYTHON" -m services.detection.run --playback "$REC" --server "http://localhost:$PORT" || true
  echo "Playback finished -- the dashboard stays up at http://localhost:$PORT/ (Ctrl-C to stop)."
  wait "$SERVER_PID"
else
  VIDEO="${POS[0]:-0}"
  AUDIO="${POS[1]:-}"
  ARGS=(--video "$VIDEO" --server "http://localhost:$PORT")
  # a video file plays once in live mode (a recorded session); demo mode loops it
  if [ "$MODE" = "live" ]; then ARGS+=(--no-loop); else ARGS+=(--loop); fi
  if [ -n "$AUDIO" ]; then ARGS+=(--audio "$AUDIO"); fi
  ARGS+=(${EXTRA[@]+"${EXTRA[@]}"})
  echo "Detection: python -m services.detection.run ${ARGS[*]}"
  "$PYTHON" -m services.detection.run "${ARGS[@]}" || true
  echo "Detection finished -- the dashboard stays up at http://localhost:$PORT/ (Ctrl-C to stop)."
  wait "$SERVER_PID"
fi
