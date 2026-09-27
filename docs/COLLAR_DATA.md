# PawPatrol Collar: Data & Control Interface

Everything the software side needs to use the collar. The collar hardware works and has been
tested: 50 Hz, 0 dropped samples, clocks in sync. Hardware build details are in `HARDWARE_GUIDE.md`.

---

## 1. What the collar is

A small unit on the dog's harness (between the shoulder blades):

| Part | Gives you |
|---|---|
| ESP32-C6 (Glyph board) | Sends data over Wi-Fi (UDP) to the laptop |
| MPU6050 motion sensor | 3-axis acceleration (g) + 3-axis rotation (deg/s) at **50 Hz** |
| Electret mic (no amplifier) | Sound **level** at the dog, plus a `loud` flag. Not good enough to classify sounds |
| Button A | Handler presses it when the dog changes activity: ground-truth **markers** |
| Button B | Spare (unused) |
| RGB LED | Shows the **rule state** on the dog: green / amber / red. Controlled by the laptop |

**Division of labour** (the project's core idea):
- **Collar + deterministic rules** → observations and instant alerts (LED).
- **Laptop webcam** → where the dog is (zones: door, bed, bowl…) and video for the demo.
- **Laptop mic** → *what* the sound is (bark / whine / howl), e.g. with YAMNet.
- **Claude** → interprets the *sequence* of events and explains it to the owner with uncertainty.
  It never gates alerts.

---

## 2. Data flow

```
collar (ESP32) --UDP :4210, 5 samples/packet--> tools/collar_receiver.py --> sessions/<date_time>/
                                                     |                        imu.csv  events.jsonl  live.json
collar  <--UDP :4211, "CALM"/"ATTN"/"ALERT"--  your code (or typing in the receiver)
collar  --"LED,<state>" report on :4210-->   receiver logs it as an `led` event
```

- The receiver must be running for data to be saved: `python tools/collar_receiver.py`
  (Windows, not WSL; WSL can't receive the collar's UDP). Use `--out sessions/<name>` to name the folder.
- **Your code should read the files, not the UDP socket.** Only one program can listen on port 4210.
- No collar or dog available? Replay a recording: `python tools/replay_session.py samples/bench_handheld_25s`
  while the receiver runs. It produces a new session exactly like live data. Use `--speed 1` when timing
  matters (faster speeds compress the timestamps).

---

## 3. Session folder

Default location `<project>/sessions/<YYYYmmdd_HHMMSS>/`. **`sessions/current.txt`** contains the absolute
path of the folder currently being written. All files are written live and are safe to read at any time.

### 3.1 `imu.csv`: every sample, 50 per second

```
t_laptop,seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,loud
1790497093.054,915,20168,0.056,-0.006,1.221,-6.2,1.5,2.0,1,0,0,0
```

| Column | Type | Meaning |
|---|---|---|
| `t_laptop` | float | Laptop Unix time (s, ms precision) when the sample was measured. **Sync key** with webcam/mic |
| `seq` | int | Sample counter from the collar. A jump = lost samples. It restarts at 0 when the collar reboots |
| `ms` | int | Collar clock, ms since boot. Steps are 20 ms (19–21) |
| `ax, ay, az` | float | Acceleration in **g**, range ±8 g. Includes gravity |
| `gx, gy, gz` | float | Rotation rate in **deg/s**, range ±1000 |
| `mic` | int | Sound level: peak-to-peak ADC counts over the last 20 ms. Quiet ≈ 1–5. Loud sounds 30+ |
| `btnA` | 0/1 | Button A held down during this sample (a press spans several rows; use `marker` events instead) |
| `btnB` | 0/1 | Button B (spare) |
| `loud` | 0/1 | The collar thinks this 20 ms was ~3× louder than its background (adaptive) |

### 3.2 `events.jsonl`: one JSON object per line, in time order

```json
{"t": 1790498783.057, "time": "2026-09-27 08:46:23.057", "type": "collar_connected", "ip": "10.204.91.128"}
{"t": 1790498783.057, "time": "2026-09-27 08:46:23.057", "type": "led", "state": "CALM"}
{"t": 1790498783.379, "time": "2026-09-27 08:46:23.378", "type": "marker"}
{"t": 1790498783.780, "time": "2026-09-27 08:46:23.780", "type": "loud", "mic": 300}
{"t": 1790498790.100, "time": "2026-09-27 08:46:30.100", "type": "collar_lost"}
```

| `type` | Extra fields | Meaning |
|---|---|---|
| `collar_connected` | `ip` | Data started or resumed |
| `collar_lost` | none | No data for 1 s (Wi-Fi drop, power bank off…). A candidate rule: "collar offline" |
| `marker` | none | Handler pressed button A: "the dog changed activity now" (ground truth) |
| `loud` | `mic` | Loud sound **at the collar**, so probably the dog itself. Max one per second |
| `led` | `state` | The LED changed: `CALM`, `ATTN`, `ALERT`, `OFF`, `RGB r g b`, or `SENSOR_FAULT`. Reported by the collar itself, so it's logged no matter who sent the command |

`t` = laptop Unix time; `time` = the same, readable.

### 3.3 `live.json`: latest reading, rewritten ~10×/s

```json
{"t_laptop": 1790498784.081, "time": "2026-09-27 08:46:24.081", "collar_ms": 6100, "seq": 55,
 "ax": 0.01, "ay": 0.02, "az": 1.19, "gx": 1.0, "gy": 2.0, "gz": 3.0,
 "mag": 1.19, "pitch": -0.5, "roll": 1.0, "mic": 5, "loud": 0, "btnA": 0, "btnB": 0,
 "rate_hz": 49.8, "dropped": 0, "led": "CALM", "collar_ip": "10.204.91.128", "cmd_port": 4211}
```

Good for a dashboard and for "is the collar alive" (`time` should be less than ~1 s old). `collar_ip` + `cmd_port`
are what you need to control the LED.

---

## 4. Controlling the LED

Send a UDP packet with one of these strings to `collar_ip:4211` (both are in `live.json`):

| Command | LED | Intended meaning |
|---|---|---|
| `CALM` | green | All normal |
| `ATTN` | amber | Worth watching (e.g. pacing near the door for a while) |
| `ALERT` | red | A deterministic rule fired (e.g. continuous barking, impact, collar lost) |
| `OFF` | off | |
| `LED r g b` | custom (0–255 each) | |

The collar sets these by itself: **blue** = connecting to Wi-Fi, **purple** = motion sensor fault (reported as
`SENSOR_FAULT`), then green once OK.

```python
from collar_data import send_led
send_led("ALERT")            # uses sessions/current.txt -> live.json for the IP
send_led((0, 0, 255))        # custom colour
```

**Demo intent:** a rule fires, the LED turns red *instantly*, and Claude's explanation arrives seconds later. That shows
rules and LLM doing separate jobs. Keep LED logic in the rules code, never behind an LLM call.

---

## 5. Timing & syncing with webcam / laptop mic

- Every sample and event carries **laptop Unix time** (`t_laptop` / `t`). Record the webcam and mic with
  `time.time()` on the same laptop and join on it. In testing, collar and laptop time stayed within 0.06 s.
- The receiver reconstructs each sample's time from the collar clock (`ms`), so samples are evenly spaced even
  though they arrive 5 per packet.
- **Sync check** at the start of every recording session: clap in front of the webcam while pressing button A.
  The `marker` event and the clap in the audio/video should coincide; if not, apply the offset.

---

## 6. Sensor facts & quirks (measured on this collar)

| Fact | Value | What to do |
|---|---|---|
| Gravity reads **~1.20 g**, not 1.00 | cheap sensor offset | Calibrate: divide `|a|` by the resting value (`calibrate()` does it) |
| Gyro bias at rest | ≈ (-6.2, +1.4, +1.9) deg/s | Subtract the resting mean (`calibrate()` does it) |
| Noise at rest | `|a|` std ≈ 0.004 g, gyro ≈ 0.1 deg/s after bias | Anything clearly above is motion |
| Handheld motion (sample) | `|a|` std 0.06–0.14, gyro 17–160 deg/s, peaks to 2.4 g / 330 deg/s | |
| Mic | background 1–5; loud flag unreliable (weak, no amplifier) | Treat `loud` as a hint ("probably our dog"). Use the laptop mic for sound type |
| Rate | 50.0 Hz, 5 samples per UDP packet | |

**Mounting orientation: TO BE FILLED IN by the hardware person after mounting on the harness.**
Record which axis points toward the dog's head, which points up, and which points to the dog's left.
Until then, pitch/roll mean "tilt of the board", not "nose up / body roll":
```
forward (toward head) = ?    up = ?    left = ?
```

---

## 7. Suggested processing (starting points, tune on real dog data)

Use `tools/collar_data.py`:

```python
import sys; sys.path.insert(0, "tools")
from collar_data import current_session, load_imu, follow_imu, load_events, read_live, \
    calibrate, windows, window_features, send_led

rows = load_imu("samples/bench_handheld_25s")
cal = calibrate(rows[:100])                     # first 2-10 s must be still (dog standing)
for win in windows(rows, seconds=2.0, step=1.0):
    f = window_features(win, cal)               # dict: mag_std, mag_max, gyro_mean_dps, cadence_hz,
                                                # pitch_deg, roll_deg, mic_max, loud, marker, t_start, t_end
```

Sample output on the bench recording (2 s windows):
```
 0.0s mag_std=0.004 gyro=  0.1   <- still
 4.0s mag_std=0.136 gyro= 50.6   <- moving
10.0s mag_std=0.071 gyro=157.6   <- moving (rotating)
18.0s mag_std=0.004 gyro=  0.1   <- still
```

Initial rule-of-thumb thresholds (after calibration; **must be tuned on the dog recording**):

| Activity | Signal |
|---|---|
| Still (lying / sitting / standing) | `mag_std < 0.02` and `gyro_mean_dps < 5` |
| Lying vs sitting vs standing | posture from `pitch_deg` / `roll_deg`, compared with the calibration pose (needs the mounting orientation) |
| Sniffing | low `mag_std` (0.02–0.06) + head-down pitch + frequent small rotations |
| Walking | `mag_std` ~0.05–0.15, `cadence_hz` ~1–2 |
| Trotting | higher `mag_std`, `cadence_hz` ~2–3 |
| Galloping | `mag_max` > ~2.0, `cadence_hz` 3+ (probably not in our recording) |
| Impact / fall | `mag_max` > ~3 in a single window → deterministic alert |

`cadence_hz` is only meaningful while moving (`mag_std > 0.03`). When still it's noise.

Turn per-window labels into **events** (merge consecutive equal labels into segments with start/end/duration),
then combine with webcam zones and laptop-mic sound classes into the timeline Claude reads.

---

## 8. Recordings

| Path | What |
|---|---|
| `samples/bench_handheld_25s/` | Real collar data, 25.5 s: still ~3 s → moved by hand ~12 s → still. No markers/LED events. In git |
| `sessions/` | Live recordings (gitignored). The dog session will be recorded here, e.g. `sessions/dog1/` |

Planned dog session script (button A pressed at each change): stand still 10 s (calibration) → lie down →
sit → walk → trot → sniff treats → wait at "door" spot → vocalise (doorbell sound) → settle. Plus a bark video
played on a phone ~2 m away while the dog is quiet: the laptop mic hears a bark but the collar stays quiet,
the "not our dog" case.

---

## 9. Known limitations

- Wi-Fi: busy venue Wi-Fi caused stalls at 50 packets/s; fixed by batching. If `collar_lost` events appear
  often, switch the collar to the laptop's Mobile hotspot (2.4 GHz).
- The collar's `loud` detection is weak and intermittent. Don't build logic that depends on it.
- `seq` and `ms` restart when the collar reboots; use `t_laptop` for ordering across a whole session.
- Only one program can bind UDP 4210 (the receiver). Everything else should read the session files.
