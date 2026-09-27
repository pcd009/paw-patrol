# PawPatrol

A sensor collar + webcam + laptop mic that turn a dog's behaviour into a timeline of events. Simple rules raise
instant alerts (the collar LED); Claude explains what the sequence probably means to the owner, with uncertainty.

| Start here | For |
|---|---|
| [`docs/COLLAR_DATA.md`](docs/COLLAR_DATA.md) | Software: collar data format, LED control, timing, sensor quirks |
| [`HARDWARE_GUIDE.md`](HARDWARE_GUIDE.md) | Building and running the collar |
| [`PLAN.md`](PLAN.md) | The overall 3-hour plan and deck outline |
| [`CLAUDE.md`](CLAUDE.md) | Context for coding agents |

Quick start (Windows):
```
python tools/collar_receiver.py                                  # receive + record the live collar
python tools/replay_session.py samples/bench_handheld_25s        # or replay a recording (receiver running)
```
