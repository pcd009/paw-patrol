# 🐾 Paw Patrol

**A dog monitor that explains, not just detects.** A camera, a microphone and a sensor collar we built watch a dog
while its owner is away. Deterministic rules raise alerts instantly, and **Claude turns the stream of behaviour into a
plain-language, uncertainty-aware picture of the dog's day**: what happened, what it probably means, and whether
the owner needs to do anything.

> Built in one day at Claude Build Day by a team of two: hardware collar + software pipeline, both built with Claude.

---

## Contents

1. [The problem](#1-the-problem)
2. [What Paw Patrol does](#2-what-paw-patrol-does)
3. [How it works](#3-how-it-works)
4. [The collar (hardware)](#4-the-collar-hardware)
5. [The pipeline (software)](#5-the-pipeline-software)
6. [How Claude is used](#6-how-claude-is-used)
7. [Results](#7-results)
8. [Running it](#8-running-it)
9. [Repository layout](#9-repository-layout)
10. [Limitations & honest caveats](#10-limitations--honest-caveats)
11. [Roadmap](#11-roadmap)
12. [Team & credits](#12-team--credits)

---

## 1. The problem

Owners who work all day can't see what their dog is doing. Existing pet cameras **classify single moments**
(*"Bark detected"*, *"Motion detected"*) and fire static alerts. A single behaviour means very different things
depending on what came before it, where the dog is, how long it lasted, and what's normal for that dog:

- Three barks after a delivery van passes → **normal**.
- Whining at the door for ten minutes right after the owner left, then settling on the bed → **mild separation
  behaviour, no action needed**.
- Barking and whining that keeps escalating for minutes → **worth checking now**.
- A bark from the TV or next door → **not your dog at all**.

**Core question:** can observable dog behaviour, detected by sensors and ML, be combined with context to give owners
something more useful than isolated classifications or static alerts?

We deliberately **do not claim to translate what a dog thinks**. We turn low-level observations into context a human
can understand and act on, and we say how sure we are.

---

## 2. What Paw Patrol does

An **owner dashboard** (`http://localhost:8000`) shows:

| Section | What it shows |
|---|---|
| **Live view** | The camera feed and what the dog is doing right now (*"Resting, for 25 min"*) |
| **Today** | A Claude-written check-in: headline, a 2–3 sentence story of the day, highlights, and *"anything to do?"* |
| **Tiles** | Active minutes vs. daily goal, rest time, longest nap, barks/whines, door-button presses, collar status |
| **Day at a glance** | A 15-minute colour strip of the whole day + time spent per activity |
| **Moments** | Photos of naps, zoomies, door requests |
| **Recent changes / Alerts & sounds** | The last behaviour changes and every alert with its reason |
| **Ask Paw Patrol** | A chat box: ask anything about the day, answered by Claude from the recorded events |

On the dog, the **collar LED mirrors the rule state**: 🟢 calm · 🟠 worth watching · 🔴 alert. It changes the instant a
rule fires, seconds before Claude's explanation arrives. That split is the design: **rules for safety, Claude for
meaning.**

Behaviours recognised: `walking`, `trotting`, `galloping`, `sitting`, `standing`, `lying_on_chest`, `sniffing`,
`barking`, `whining`, `button_press`.

---

## 3. How it works

```
 ┌─────────────── SENSING ───────────────┐
 │ Webcam ──► YOLO11n dog box + gait      │
 │          └► Claude vision: posture,    │
 │             sniffing (every ~2 s)      │
 │ Laptop mic ──► AST sound classifier:   │      ┌──────────── FUSION ────────────┐
 │                bark / whine            │ ───► │ collar decides gait while      │
 │ Collar (ESP32 + MPU6050 + mic, 50 Hz)  │      │ moving; "still" corrects video │
 │  ──► gait: still/walk/trot/gallop      │      │ collar mic says WHOSE bark     │
 │  ──► loudness at the dog               │      │ (ours vs TV / next door)       │
 └────────────────────────────────────────┘      └───────────────┬────────────────┘
                                                                 ▼
                                             Segmenter (~1 s stability) ──► BehaviorEvent
                                                  (validated against JSON Schemas)
                                                                 │
                           ┌─────────────────────────────────────┼──────────────────────────┐
                           ▼                                     ▼                          ▼
              DETERMINISTIC RULE ENGINE                    Context packet            Day history
              (the ONLY source of alerts)                (last 60 s of events        (15-min strip,
              → Alert → collar LED + dashboard            + alerts)                   moments, tiles)
                                                                 │                          │
                                                                 ▼                          ▼
                                                  CLAUDE triage (advisory):      CLAUDE daily digest
                                                  routine / monitor / notify /   + "Ask Paw Patrol"
                                                  insufficient_evidence,
                                                  owner message, uncertainty,
                                                  cited event ids (checked in code)
```

**Time is the glue.** Every sensor stream is stamped with the same laptop Unix clock. The collar reports laptop time
per sample, and the camera/mic recorder stores Unix time per frame and per audio start. Recordings made on another
device (e.g. an iPhone) are aligned with a **clap + collar button press** at the start of each session.

---

## 4. The collar (hardware)

Lives in [`paw-patrol/`](paw-patrol/) (also its own repo: `github.com/Devesh99/paw-patrol`). Full build guide:
[`paw-patrol/HARDWARE_GUIDE.md`](paw-patrol/HARDWARE_GUIDE.md). Data interface:
[`paw-patrol/docs/COLLAR_DATA.md`](paw-patrol/docs/COLLAR_DATA.md).

### 4.1 Parts

| Part | Role |
|---|---|
| **ESP32-C6** (PCBCupid Glyph C6) | Reads the sensors, streams over Wi-Fi (UDP), drives the LED |
| **MPU6050** accelerometer + gyroscope | Motion and posture at 50 Hz (±8 g, ±1000 °/s) |
| **Electret microphone** + 2.2 kΩ bias resistor | Loudness *at the dog*: tells our dog's bark from other sounds |
| **RGB LED** (common anode) | Shows the rule state on the dog |
| **Push button A** | Handler's "activity changed now" marker: ground-truth labels + clock sync |
| **Push button B** | Spare |
| USB power bank | Power on the dog (the board also supports a LiPo) |

### 4.2 Wiring (labels as printed on the Glyph board)

| Glyph pin | Connected to |
|---|---|
| 3.3V | MPU6050 VCC · LED common (long) leg · 2.2 kΩ → mic + |
| GND | MPU6050 GND · mic − · button A · button B |
| SDA (IO4) / SCL (IO5) | MPU6050 SDA / SCL |
| A2 (IO2) | Mic + (analog) |
| D6 (IO6) | Button A (to GND, internal pull-up) |
| A3 (IO3) | Button B |
| D18 / D19 / D20 | LED red / green / blue (via 220–330 Ω) |

IO8/IO9 (boot strapping) and IO12/IO13 (USB) are left free. The full diagram is in the hardware guide, section 2b.

### 4.3 Firmware ([`paw-patrol/firmware/collar/collar.ino`](paw-patrol/firmware/collar/collar.ino))

- Samples the MPU6050 at **50 Hz**. Between samples it reads the mic continuously and reports its peak-to-peak level
  per 20 ms slot, with an **adaptive "loud" flag** (~3× the learned background). It flashes the onboard LED on loud sounds.
- Sends **5 samples per UDP packet** (10 packets/s). Our first version sent 50 packets/s and stalled on busy venue
  Wi-Fi (bursts, gaps, ~24 Hz). Batching fixed it: 50.0 Hz, 0 lost.
- Listens on UDP **4211** for LED commands (`CALM`, `ATTN`, `ALERT`, `OFF`, `LED r g b`) and **reports every LED
  change back**, so each one is logged whoever triggered it.
- Self-diagnostics on the LED: 🔵 connecting to Wi-Fi, 🟣 motion sensor fault. It recovers automatically from a loose
  sensor wire.

### 4.4 Laptop side

| Tool | What it does |
|---|---|
| `paw-patrol/tools/collar_receiver.py` | Receives the stream and writes a **session folder**: `imu.csv` (every sample, laptop-timestamped), `events.jsonl` (markers, loud sounds, LED changes, connect/lost, recording start/stop), `live.json` (latest reading, 10×/s). Type `calm` / `alert` to drive the LED. `--stop-after-lost 10` ends the recording when the collar is switched off |
| `paw-patrol/tools/collar_data.py` | Helpers: load/follow a session, calibrate, 2-second windows, explainable features (motion energy, cadence, tilt, rotation), `send_led()` |
| `paw-patrol/tools/replay_session.py` | Replays a recording as if the collar were live, so the software works without hardware or a dog |

The hardware and software share **no code**. The contract is the session-folder file format, so either side can
change independently.

---

## 5. The pipeline (software)

Lives in [`dog-behavior-system/`](dog-behavior-system/) (Python, FastAPI, vanilla-JS dashboard). Details in
[`dog-behavior-system/README.md`](dog-behavior-system/README.md).

### 5.1 Detection

| Input | Model / method | Output |
|---|---|---|
| Camera | **YOLO11n** (COCO class 16 = dog) → largest dog box, box-centre speed → gait heuristic | walking / trotting / galloping / standing |
| Camera | **Claude vision**: a strip of 4 dog crops over 1.2 s, every ~2 s, confidence-weighted vote over the last 3 answers | posture (sitting, standing, lying), sniffing |
| Laptop mic | **AST** (Audio Spectrogram Transformer, pretrained on AudioSet), 1 s windows | barking (`Bark`, `Bow-wow`, `Yip`, `Dog`), whining (`Whimper`, `Howl`) |
| Collar | Calibrated motion features: motion energy, step cadence, peak g, rotation | still / walking / trotting / galloping |
| Collar (offline) | Trained **RandomForest** (see §7) | 7-class behaviour. Evaluated offline, **not yet wired into the live path**: the collar sends 50 Hz, the model expects 100 Hz |
| Dog button | `POST /api/sensor/button` | button_press |

A **segmenter** turns frame-level labels into events once a label is stable for ~1 s. Long behaviours are re-posted
every 5 s so the timeline stays live. Every event is validated against a frozen JSON Schema
(`dog-behavior-system/contracts/schemas/`) before and after it reaches the server.

### 5.2 Fusion (`services/fusion.py`): how the collar and camera combine

- **Gait: the collar wins while the dog travels.** It doesn't care about camera angle, panning or the dog leaving the
  frame. When the collar says the dog is **still**, a video "walking" (often camera motion) becomes "standing".
- **Posture and sniffing stay with Claude vision.** The camera sees the body shape.
- **Sound: the laptop mic says *what* it is, the collar mic says *whose* it is.** Loud at the collar → **our dog**.
  Quiet at the collar → **probably not our dog** (TV, phone, next door), which is **excluded from bark alerts**.
- **Off camera:** the collar keeps reporting movement.

### 5.3 Deterministic rules (`services/context_rules/engine.py`): the only thing that creates alerts

| Rule | Condition | Severity |
|---|---|---|
| Repeated barking | 3+ bark events within 60 s | warning |
| Sustained whining | > 15 s of whining within 60 s | warning |
| Prolonged distress | 6+ bark/whine events within 2 min | **critical** |
| Door request | Dog pressed the door button | info |

Pure functions, no I/O and no LLM. A rule won't re-fire until its window has passed, and stale alerts auto-resolve.
Barks the collar attributes to another dog never count. The alert state drives the **collar LED** and the dashboard.

---

## 6. How Claude is used

**Model: Claude Opus 5.5** (`claude-opus-5-5`), selected with the `CLAUDE_MODEL` environment variable. All calls go
through one choke point, `services/llm_common.py`, which requests structured JSON output and records which model
actually answered. If there's no API key, or the API errors or refuses, a deterministic mock answers instead, and the
dashboard badge always says which one ran (`AI: Claude (live)` vs `AI: mock`).

| Where | What Claude does |
|---|---|
| **Vision labeller** | Looks at short strips of dog crops and labels posture, sniffing and mouth movement, with a confidence |
| **Triage** (every ~15 s, over the last 60 s) | Reads the context packet (events + alerts) and returns `routine` / `monitor` / `notify` / `insufficient_evidence`, a 1–2 sentence owner message, an explicit **uncertainty** statement, a suggested check, and the event/alert IDs that support it |
| **Daily digest** | Writes the "Today" check-in from the day summary (not raw events): headline, story, highlights, anything to do |
| **Ask Paw Patrol** | Answers owner questions from the whole day's history |

**Guardrails, enforced in code, not just in the prompt:**
- Claude **cannot create, escalate, dismiss or modify alerts**. Only the rule engine can.
- Every event/alert ID Claude cites is **checked to be in the packet it was given**. If it cites anything else, the
  decision is downgraded to `insufficient_evidence` instead of trusting a possibly hallucinated citation.
- The prompt forbids claims about what the dog "thinks" or "feels", asks for hedged explanations, and prefers
  `monitor` / `insufficient_evidence` over `notify` when evidence is thin. It never guesses a location.
- Owner-facing text contains no IDs, confidence numbers or system jargon.

**Claude also built the project.** Both halves were written with Claude Code (Opus 5.5): the collar firmware,
receiver and debugging from pasted serial/receiver output, the detection pipeline, rules, fusion, time sync, dashboard,
and the IMU classifier training. The hardware side's Claude wrote a data spec and `CLAUDE.md`, and the software side's
Claude read them and integrated the collar without a meeting.

---

## 7. Results

### Collar (measured on the real device)

| Metric | Result |
|---|---|
| Sample rate | **50.0 Hz**, samples 19–21 ms apart |
| Lost samples | **0** (after batching; the first version lost/stalled on venue Wi-Fi) |
| Laptop ↔ collar clock | within **0.06 s** |
| Noise at rest (after calibration) | \|a\| std ≈ 0.004 g, gyro ≈ 0.1 °/s |
| Still vs moving (bench test) | motion energy 0.004 vs 0.06–0.14; rotation 0.1 vs 17–158 °/s: clearly separable |
| Real dog session (`dog1`) | **97 s, 4,850 samples, 0 lost** |

Sensor quirks handled by calibration: this MPU6050 reads ~1.20 g at rest, and the gyro has a bias of
≈ (−6.2, +1.4, +1.9) °/s.

### IMU behaviour classifier

RandomForest (200 trees) on 34 per-window features (2 s windows, 1 s hop), trained on the public **Vehkaoja et al.
2022** dog movement dataset: ~45 dogs including 3 Labradors, collar (neck) sensor only, 10.6 M rows. **Tested on dogs
held out entirely from training**, 7,660 windows:

| Behaviour | F1 |
|---|---|
| sniffing | 0.99 |
| trotting | 0.99 |
| walking | 0.96 |
| lying on chest | 0.68 |
| standing | 0.44 |
| sitting | 0.41 |
| galloping | too few test samples |
| **Overall accuracy** | **0.79** |

Movement is reliable. Static postures are hard to tell apart from a neck sensor alone, which is exactly why posture
comes from Claude vision and the gait from the collar. The live system currently uses the calibrated threshold features
(§5.1). Plugging this model in needs a 50 → 100 Hz resample and a unit check against the real collar.

---

## 8. Running it

### 8.1 Software (Mac/Linux)

```bash
cd dog-behavior-system
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...              # optional: without it, the mock answers
export CLAUDE_MODEL=claude-opus-5-5       # otherwise the default in config.yaml is used

scripts/run_demo.sh replay 1                              # canned 60 s story, no camera/mic/key needed
scripts/run_demo.sh live 0 mic                            # live webcam 0 + mic, starts empty
scripts/run_demo.sh demo data/demo_videos/running-1.mp4   # ~3 h sample day, then live events on top
# open http://localhost:8000
```

### 8.2 Collar

1. Wire it (§4.2) and flash `paw-patrol/firmware/collar/collar.ino` with Arduino IDE (board: **GLYPHC6**). Set Wi-Fi
   name/password and the laptop's IP at the top. Use a phone hotspot or the laptop's Mobile hotspot on **2.4 GHz**.
   Venue Wi-Fi often blocks device-to-device traffic.
2. On the laptop (Windows needs a firewall rule for UDP 4210):
   ```bash
   python paw-patrol/tools/collar_receiver.py --out paw-patrol/sessions/dog1 --stop-after-lost 10
   ```
3. Live with the dashboard: `scripts/run_demo.sh live 0 mic --collar ../paw-patrol/sessions --record dog1`
4. No collar at hand: `python paw-patrol/tools/replay_session.py paw-patrol/samples/bench_handheld_25s` (receiver running).

### 8.3 Recording a session and playing it back in sync

```bash
# collar laptop: records until Ctrl-C or the collar is off for 10 s
python paw-patrol/tools/collar_receiver.py --out paw-patrol/sessions/dog1 --stop-after-lost 10
# phone / Mac camera: record normally. At the start, press collar button A while clapping once on camera.
cd dog-behavior-system
.venv/bin/python -m scripts.process_session IMG_1234.MOV --collar ../paw-patrol/sessions/dog1 --check-sync
.venv/bin/python -m scripts.process_session IMG_1234.MOV --collar ../paw-patrol/sessions/dog1
scripts/run_demo.sh playback data/recordings/IMG_1234
```

The video start time comes from its metadata. Each frame keeps its own timestamp (phones use a variable frame rate),
and the clap is lined up with the button-A marker for exact sync across the two clocks.

### 8.4 Dog session protocol (~3–5 min, button A at every change)

Stand still 10 s (calibration) → lie down → sit → walk → trot → sniff scattered treats → wait at the "door" →
vocalise (doorbell sound) → settle. Plus: play a bark video on a phone ~2 m away while the dog is quiet. The laptop
hears a bark, the collar stays quiet, and it's correctly treated as *not our dog*.

---

## 9. Repository layout

```
.
├── README.md                        ← this file
├── dog-behavior-system/             ← SOFTWARE
│   ├── app/orchestrator/server.py   FastAPI: events in → rules + triage → state out, dashboard
│   ├── app/dashboard/index.html     owner dashboard (vanilla JS, polls /api/state every 1 s)
│   ├── app/io/                      video/audio/sensor inputs, collar reader, recording, output sinks
│   ├── services/detection/          YOLO video detector, AST audio detector, Claude vision labeller,
│   │                                IMU features, segmenter
│   ├── services/fusion.py           collar + camera + mic fusion
│   ├── services/context_rules/      deterministic rule engine (only source of alerts)
│   ├── services/llm_triage/         Claude triage + "Ask Paw Patrol"
│   ├── services/summary/            daily summary, Claude digest, demo story
│   ├── contracts/                   frozen JSON Schemas + fixtures + validator
│   ├── scripts/                     run_demo.sh, record, process_session, train_imu, eval_clips
│   ├── config.yaml                  dog profile, zones, thresholds, collar settings, model
│   └── docs/                        RESULTS.md, DATASETS.md, DEMO_FOOTAGE.md
└── paw-patrol/                      ← HARDWARE (subtree of github.com/Devesh99/paw-patrol)
    ├── firmware/collar/collar.ino   ESP32-C6 collar firmware
    ├── tools/                       collar_receiver.py, collar_data.py, replay_session.py
    ├── docs/COLLAR_DATA.md          data + LED control interface
    ├── HARDWARE_GUIDE.md            build guide, wiring diagram, troubleshooting
    ├── samples/                     real collar recordings to develop against
    └── PLAN.md                      the original 3-hour plan
```

---

## 10. Limitations & honest caveats

- **Not a dog translator.** Everything is observed behaviour plus hedged interpretation. Claude states its uncertainty.
- **Thresholds are starting points.** Gait thresholds, zones and collar cut-offs were tuned by eye, on a bench, and on
  one short dog session. They need tuning per dog and per room.
- **Static postures from the collar are weak** (sitting/standing F1 ≈ 0.4). Posture relies on the camera.
- **The collar mic has no amplifier.** It measures loudness only, and its "loud" detection is intermittent. It answers
  "was it our dog?", never "what sound was it?".
- **Single dog, single camera.** The detector follows the largest dog box.
- **Prototype hardware:** jumper wires and a power bank on a harness. Venue Wi-Fi can drop the stream; a local
  hotspot is more reliable.
- **Rule thresholds** (3 barks/60 s, etc.) are sensible defaults, not veterinary guidance.

---

## 11. Roadmap

- **A real collar:** a custom PCB with a mic preamp, a LiPo and an enclosure. Classification **on the collar**
  (TinyML), so it works without a laptop.
- **Per-dog baselines:** learn what's normal for *this* dog, so "unusual" means unusual for them.
- **Longitudinal insight:** separation-anxiety trends, activity vs. goal over weeks, sleep quality.
- **Vet-shareable weekly reports** generated by Claude from the history.
- **More inputs:** multiple cameras/rooms, multi-dog homes, a dog-pressable door button (already supported in the API).
- **Owner feedback loop:** "this alert was useful / not useful" tunes rules and explanations.

---

## 12. Team & credits

- **Devesh**: hardware collar: electronics, firmware, receiver, collar data pipeline
- **Priyanshu**: software: detection, fusion, rules, Claude integration, dashboard

Built at **Claude Build Day** with **Claude Opus 5.5** via Claude Code.

**Data & models:** Vehkaoja et al. 2022, *Movement sensor dataset for dog behavior classification* (Mendeley Data,
DOI 10.17632/vxhx934tbn, CC BY 4.0) · Ultralytics **YOLO11n** (COCO) · **AST** Audio Spectrogram Transformer (AudioSet)
· **ESC-50** dog-bark clips for testing · PCBCupid Glyph C6 documentation.
