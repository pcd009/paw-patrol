# PawPatrol: project context for agents

Claude Build Day prototype (3-hour build). Problem: pet cams classify single behaviours ("BARK DETECTED");
owners need *context*. We combine a sensor collar + webcam + laptop mic into a timeline of behaviour events.
**Deterministic rules** raise alerts (and set the collar LED); **Claude** interprets the event sequence and
explains it to the owner with uncertainty. It never claims to read the dog's mind and never gates alerts.
Judging: new model capability, works live, would you use/share it, demo clarity in 2 minutes.
Deliverables: 1-minute demo video + slide deck (bigger vision in the deck, small working prototype).

## Read these
- `docs/COLLAR_DATA.md`: **the collar data and LED control interface** (file formats, units, timing, sensor quirks,
  suggested features/thresholds). Start here for any software that uses the collar.
- `tools/collar_data.py`: helpers: `current_session`, `load_imu`, `follow_imu`, `load_events`, `read_live`,
  `send_led`, `calibrate`, `windows`, `window_features`. Standard library only.
- `PLAN.md`: overall 3-hour plan (hardware + software + deck). `HARDWARE_GUIDE.md`: how the collar was built.

## Key facts
- Collar data arrives only via `tools/collar_receiver.py` (runs on **Windows**, UDP 4210). It writes
  `sessions/<date_time>/imu.csv | events.jsonl | live.json`, and `sessions/current.txt` points to the active one.
  Other code reads those files; don't bind port 4210 yourself.
- 50 Hz accel (g) + gyro (deg/s); timestamps are laptop Unix time (`t_laptop`/`t`), which is the sync key with the webcam/mic.
- Sensor reads ~1.2 g at rest and has gyro bias: always `calibrate()` on a still stretch first.
- LED: send `CALM`/`ATTN`/`ALERT` (green/amber/red) via `send_led()`; each change is logged as an `led` event.
- Collar mic only gives loudness at the dog ("was it our dog?"). Sound *classification* uses the laptop mic.
- Develop without hardware: `python tools/replay_session.py samples/bench_handheld_25s` with the receiver running.

## Repo conventions
- `sessions/` and `laptop-ip` are gitignored (recordings; local Wi-Fi credentials). Never commit Wi-Fi passwords;
  `firmware/collar/collar.ino` in git keeps placeholder credentials.
- Team of 2: hardware person owns `firmware/` and `tools/collar_*`; software person owns their own folder.
