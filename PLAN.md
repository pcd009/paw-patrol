# PawPatrol — 3-hour hardware prototype plan

## Context
Claude Build Day. Goal: a prototype that runs live on a real dog and shows the core idea in under a minute:
deterministic sensors/rules make the *observations*, Claude turns the *sequence* of observations into
an uncertainty-aware explanation an owner can act on. Judged on New Capability, It Works, Keep/Share, Demo Clarity.
Deck carries the bigger vision; the prototype only needs to prove one loop end-to-end.

## The one-line demo
Split screen: dog on webcam | live timeline of detected events | Claude's message.
Left caption: what a normal pet cam sends — `BARK DETECTED` ×6.
Right caption: what PawPatrol sends — *"Bruno trotted between the door and the window for 4 min, then whined twice
while standing at the door. This pattern usually shows anticipation (e.g. hearing someone outside), not distress —
he settled on his bed after. Confidence: medium. No action needed."*

## Architecture (single ESP32 → collar; laptop = brain)
```
[Collar]  ESP32-C6 Glyph + MPU6050 + RGB LED + button, LiPo (onboard charger) or small power bank
          streams IMU @50 Hz + button events over Wi-Fi UDP
[Laptop]  webcam  → YOLOv8n (RTX 3050): dog bbox → zone (door / bed / bowl / window)
          laptop mic → YAMNet: Bark / Whimper / Howl classes
          IMU windows (2 s) → activity: lying / sitting / standing / walking / trotting / galloping / sniffing
          event builder → timeline of structured events (start, end, confidence, zone)
          rules engine (deterministic alerts)  ──┐
          Claude (every ~60 s or on rule trigger) ← timeline + 2-3 keyframes → explanation + confidence
          dashboard (single web page) shows video, timeline, rule alerts, Claude messages
          alert state pushed back to collar → RGB LED (green calm / amber attention / red rule alert)
```

## Hardware decisions (honest trade-offs)
- **Collar node = ESP32 + MPU6050 only, no breadboard.** F-F jumpers straight from IMU to board, heat-shrink/tape,
  mount on the *back of a harness* (more stable orientation than a collar, easier to fix power). Board has LiPo
  charging + slide switch; else a small USB power bank in the harness.
- **Condenser mic: don't rely on it for classification.** A bare electret + 2.2k bias gives millivolt signal; the
  ESP32 ADC without an op-amp preamp will only catch very loud, close sounds. Use it at most as a loudness-envelope
  "vocalization spike" on the collar (dog's throat is right there — that's a nice pitch point), and do real audio
  classification with the laptop mic + YAMNet. Drop entirely if it eats >15 min.
- **16x2 LCD: bench only / skip.** It can't live on a collar and we have one MCU. If there's a spare 15 min, it's a
  "base station" prop for the video; otherwise drop. Owner-facing output = the dashboard (phone-viewable).
- **Buttons = ground-truth labeling** during the real-dog session:
  button A = "event marker" (handler presses when something notable happens) → lets us show accuracy vs truth.
- **RGB LED on collar** = rule-engine state, visible in the video.
- Pins (verify against the Glyph C6 MCP/pinout before wiring): avoid strapping GPIO8/9/15 and USB GPIO12/13;
  onboard LED is GPIO14. MPU6050 on any two free GPIOs as I2C SDA/SCL at 3.3 V; mic (if used) on an ADC pin
  (GPIO0–6); RGB LED on 3 free GPIOs via PWM with resistors; buttons with INPUT_PULLUP.

## IMU activity classifier (fast, explainable)
Per 2 s window, 50% overlap, after a 3 s "standing still" calibration to get gravity direction:
- **Posture** from gravity vector pitch/roll → lying on chest / sitting / standing (sitting = strong nose-up pitch
  on a harness).
- **Motion energy** (std of |a|) → still vs moving.
- **Gait cadence** = dominant FFT freq of |a| + amplitude → walk (~1–2 Hz, low amp) / trot (~2–3 Hz) / gallop
  (high amp, 3+ Hz, big vertical swings).
- **Sniffing** = low-energy movement with head-down pitch.
Start with thresholds tuned on 1–2 min of the real dog; if time allows, fit a kNN/RandomForest on button-labeled
windows. (Deck mention: the public Kumpulainen/Vehkaoja collar+harness movement dataset uses exactly these 7
behaviors — the path to a trained model.)

## Rules vs LLM (the thesis, keep it visible in the UI)
Rules (deterministic, instant, LED + alert): continuous barking > N s; galloping + barking combo; no movement for
X min while not in bed zone; high-g impact spike; collar data lost.
Claude (interpretation only, never gates alerts): receives the last ~10 min timeline JSON + per-dog baseline stats +
keyframes; returns `{summary, possible_interpretations[{meaning, likelihood}], confidence, suggested_action,
what_would_change_my_mind}`. Prompt forbids claiming to know the dog's thoughts.

## New Capability angle
Pick ONE concrete thing to show and state it on a slide: Claude reasoning jointly over a raw multi-sensor time series
+ camera frames in a single call, producing calibrated alternatives rather than a label. Verify against the actual
release notes for this model vs the previous one before putting a claim on a slide — I (the model) shouldn't be the
source for that comparison.

## Constraint: only a few minutes with a dog, team of 2 → "record once, replay forever"
- Build a **capture mode first** (by ~0:45): one command records the webcam video, laptop audio, and IMU UDP stream,
  all timestamped with the laptop clock. Save to `sessions/<ts>/`.
- The whole pipeline (classifier, rules, Claude, dashboard) runs on a **replay** of a session, exactly as it
  would live. That's how we develop, tune thresholds, and film the demo. Being honest that the demo is real data
  replayed is fine; being able to run it live too is a bonus.
- **Dog-session script (~3–5 min, printed on paper):** 10 s standing still (calibration) → lying → sit on command →
  walk on leash → trot/jog with handler → sniffing a treat on the floor → a bark/whine trigger (doorbell sound,
  a treat held just out of reach) → walk to the "door zone" and wait. Press button A at each change = labels.
- Before the dog session, dry-run the whole capture with a human carrying the harness so nothing fails on the dog.
- Galloping probably won't happen on command: fine, show it as "supported, not demoed".
- If there's no dog time at all: film a pet dog at home tonight with a phone for video/audio, and fake the IMU
  part (clearly labelled) only as a last resort.

## 3-hour timeline (2 people)
| Time | HW person | SW person |
|---|---|---|
| 0:00–0:30 | Wire MPU6050 + LED + button; firmware: Wi-Fi UDP stream of `ts,ax,ay,az,gx,gy,gz,btn` @50 Hz; LED control via UDP | UDP receiver + **capture/replay recorder** (webcam + mic + IMU) |
| 0:30–0:50 | Harness mount + power; dry-run capture with a human | YOLOv8n dog bbox + zones; YAMNet bark/whine |
| ~0:50–1:00 | **Dog session (the few minutes), follow the script** | Operate the laptop, watch the capture |
| 1:00–1:45 | IMU features + threshold classifier tuned on the recording (use button labels) | Event builder + rules engine + Claude call |
| 1:45–2:15 | LED feedback from rules; accuracy vs labels for the slide | Dashboard in replay mode; polish the Claude prompt |
| 2:15–2:30 | Screen-record the demo from replay | Freeze code |
| 2:30–3:00 | Cut the 1-min video | Deck |

If the dog time slot moves, shift the session and keep building against the dry-run capture until then.

## Deck — bigger goal (6–7 slides)
1. Problem: pet cams classify, they don't explain. 2. Insight: meaning lives in sequence + context.
3. Architecture: sensors → events → rules (safety) → Claude (interpretation). 4. Live demo still / numbers.
5. Why the new model matters. 6. Roadmap: custom collar PCB w/ preamp mic, on-device TinyML, per-dog baseline learning,
separation-anxiety tracking, vet-shareable weekly reports, multi-pet homes. 7. Honest limits: no "dog translation".

## Verification
- Collar: shake test → gait cadence plot matches (walk with it in hand ≈ 2 Hz).
- Real dog: compare classifier output vs video for 3 min; report rough accuracy on the slide.
- Rules: trigger bark alert with a recorded bark → LED turns red within 1 s, independent of Claude.
- Claude: run on recorded timeline 3× → consistent summary, includes confidence + alternatives.
