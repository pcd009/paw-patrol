# Dog Behaviour Recognition -- Hackathon Prototype

A camera + mic watch a Labrador, turn what they see/hear into structured `BehaviorEvent`s, a
deterministic rule engine raises `Alert`s from those events, and Claude Sonnet explains the
situation to the owner with explicit uncertainty -- **the LLM never creates or edits an alert.**
Every input and output sits behind a swappable adapter so tomorrow's hardware (an ESP32
dog-pressable button, an ESP32 LED/buzzer mirror, maybe an MPU6050 IMU collar) plugs in without
touching detection/rules/LLM code.

## Quickstart

```bash
python3.11 -m venv .venv          # 3.11 recommended; 3.9 (system python3) also works
.venv/bin/pip install -r requirements.txt

# Replay the canned 60s demo story end-to-end (no camera/mic/API key needed):
scripts/run_demo.sh replay 1      # 1x = real time; try 15-30x to see it fly by
# then open http://localhost:8000/

# Or run live off a webcam (+ optional mic):
scripts/run_demo.sh live 0 mic
```

`ANTHROPIC_API_KEY` is optional. If it's unset (or the API errors/refuses), every Claude call
falls back to a deterministic mock so the demo still runs end-to-end -- the dashboard's model
badge always says which one happened (`model: mock` vs `model: claude-sonnet-5`).

Run the pieces independently if you like:
```bash
python -m app.orchestrator.server                 # the API + dashboard, port 8000
python -m services.detection.run --video 0 --audio mic --server http://localhost:8000
python3 -m contracts.validate                      # check every fixture against its schema
```

## Architecture

```
 VideoSource ---\                                    +-----------------+
                 >-- video_detector.py --+            |  Claude Sonnet  |
 (webcam/file/   |   (YOLO + gait        |            |  (vision, low   |
  RTSP url)      |    heuristic +        |            |   effort JSON)  |
                 |    vision_labeler)    |            +---------^-------+
 AudioSource ----/                       |                      |
 (mic/file)          audio_detector.py --+--> segmenter.py --> BehaviorEvent
                     (AST pretrained)         (~1s stability)   (validated against
                                                                  contracts/schemas)
 SensorSource (button POST /api/sensor/button,               |
  stub Serial/Imu for tomorrow)  ----------------------------->|
                                                                v
                                          POST /api/events  (app/orchestrator/server.py)
                                                                |
                                       services/context_rules/engine.py
                                       (evaluate_rules, deterministic, only source of Alerts)
                                                                |
                                              build_context -> ContextPacket
                                                                |
                                        services/llm_triage/triage.py (Claude, mock fallback)
                                        -> TriageResult (advisory only, ids validated as a
                                           subset of the packet -- never trusted blindly)
                                                                |
                                      DemoState (events+alerts+context+triage+device)
                                                ed
                         GET /api/state (dashboard polls @ 1s)   GET /api/device (plain text,
                         app/dashboard/index.html                 ESP32 LED/buzzer mirror polls)
```

Everything is validated against `contracts/schemas/*.json` (frozen contracts) before it's
accepted: `services/detection/run.py` validates every event before POSTing it, and the
orchestrator validates again on ingest, plus every `ContextPacket`/`TriageResult` it builds.

## Hardware plug-in points (for tomorrow)

Nothing above the adapter layer needs to change. Edit `config.yaml` and/or swap a class:

| Hardware | Where it plugs in | What to change |
|---|---|---|
| ESP32 dog-pressable button (Wi-Fi) | `POST /api/sensor/button` (no body needed -- the orchestrator builds the whole event) | Nothing in Python. Point the ESP32's HTTP client at `http://<laptop-ip>:8000/api/sensor/button`. |
| ESP32 button (USB-serial instead) | `app/io/inputs.py::SerialSensorSource` (stub) | Wire it into `services/detection/run.py` as a third thread, `pip install pyserial`. |
| ESP32 LED/buzzer mirror | `GET /api/device` -> plain text `led=amber buzzer=0` (no JSON lib needed on-device) | Nothing in Python -- just have the ESP32 poll that URL every ~1s and drive the LED/buzzer from the two tokens. `app/io/outputs.py::SerialSink`/`WebhookSink` are push-mode alternatives if polling doesn't fit. |
| Phone IP-cam / RTSP camera | `app/io/inputs.py::VideoSource(source)` | Set `adapters.video_source` in `config.yaml` (or `--video`) to the rtsp://\|http:// URL. |
| MPU6050 IMU collar | `app/io/inputs.py::ImuStreamSource` (stub) + `models/imu_clf.joblib` | Feed it an iterable of `(ax,ay,az,gx,gy,gz)` samples. **Must first resample to the training sample rate and rescale units to match** -- see `docs/RESULTS.md` "IMU classifier" for the exact numbers (the Vehkaoja et al. dataset's ActiGraph sensor is not the same rate/units as a raw MPU6050 reading). `services/detection/imu_features.py` has the exact feature function so training and inference never drift apart. |

Zones (`door_area`/`bed_area`/`food_area`/`play_area`) are normalized rects in `config.yaml`;
re-eyeball and edit them at the venue once the camera is mounted.

## What's mocked / stubbed

- **Claude vision (posture) and Claude triage/ask**: fall back to a deterministic mock (bbox
  aspect-ratio heuristic for posture; a fixed rule-of-thumb summary for triage/ask) whenever
  `ANTHROPIC_API_KEY` is unset, the API errors, or the model refuses. The dashboard always shows
  which happened.
- **SerialSensorSource, ImuStreamSource, SerialSink, WebhookSink**: real stubs (import
  `pyserial`/hit a URL) but not exercised without real hardware -- the demo runs entirely on the
  HTTP button endpoint + console/device-state sinks today.
- **IMU classifier**: see `docs/RESULTS.md` for whether it trained on the real Vehkaoja dataset
  or a synthetic fallback, and its per-class F1.
- **Demo footage**: see `docs/DEMO_FOOTAGE.md` if scripted download was blocked -- the pipeline
  also works directly off a webcam.

## Known limitations

- Gait thresholds (`config.yaml` `thresholds.gait`) and zone rects are tuned by eye on a
  synthetic test clip, not the real dog/venue -- expect to retune live before the demo.
- `video_detector.py` tracks only the single largest dog bounding box; multi-dog scenes aren't
  handled.
- The 1s segmenter stability window means very short/fast posture changes can be swallowed --
  intentional per the build plan, but worth knowing if something "disappears."
- Audio detection assumes a somewhat quiet room; AST was not fine-tuned, just thresholded.
- `GET /api/state` is unbounded-poll-friendly (lists are capped at the last 100-200 items) but
  the whole in-memory store resets on orchestrator restart -- nothing is persisted to disk.
- Contract fixtures (`contracts/fixtures/demo-*.json`) were regenerated from one live replay run;
  re-run `python3 -m contracts.validate` after any pipeline change that alters output shape.

## Repo layout

```
contracts/          frozen JSON Schemas + fixtures + validate.py
app/config.py        loads config.yaml
app/io/               inputs.py (VideoSource/AudioSource/SensorSource+stubs), outputs.py (sinks)
app/orchestrator/     server.py -- FastAPI: events in, rules+triage, DemoState out, dashboard
app/dashboard/        index.html -- vanilla JS, polls /api/state every 1s
services/detection/   video_detector, audio_detector, vision_labeler, segmenter, run.py
services/context_rules/engine.py   deterministic rules -- the ONLY thing that creates Alerts
services/llm_triage/   triage.py (ContextPacket -> TriageResult), ask.py (owner chat box)
services/llm_common.py single choke point for every Claude call (CLAUDE_MODEL, mock fallback)
scripts/               download_data.sh, train_imu.py, run_demo.sh
models/                yolo11n.pt, imu_clf.joblib (gitignored, regenerate via scripts/)
data/                  datasets/demo videos (gitignored)
docs/                  DATASETS.md, RESULTS.md, DEMO_FOOTAGE.md
```
