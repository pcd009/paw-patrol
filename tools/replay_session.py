"""Replay a recorded session as if the collar were live: sends its samples over UDP to the receiver.

Lets the software pipeline be developed and demoed without the collar or the dog.
    python tools/replay_session.py samples/bench_handheld_25s             # real speed, to localhost
    python tools/replay_session.py sessions/dog1 --speed 4 --loop
Run tools/collar_receiver.py at the same time: it records the replay into a new session folder exactly
like live data (fresh laptop timestamps), so downstream code can't tell the difference.
Replayed events: button markers and loud flags are inside the samples; `led` events from the recording
are re-sent as LED reports so they reappear too.
"""
import argparse
import json
import os
import socket
import time

FIELDS = "seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,loud".split(",")
BATCH = 5  # same as the firmware


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="session folder containing imu.csv (and optionally events.jsonl)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4210)
    ap.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    ap.add_argument("--loop", action="store_true", help="start again at the end")
    args = ap.parse_args()

    with open(os.path.join(args.session, "imu.csv")) as f:
        header = f.readline().strip().split(",")
        idx = [header.index(k) for k in FIELDS]
        t_idx = header.index("t_laptop")
        rows = [line.strip().split(",") for line in f if line.strip()]
    samples = [(float(r[t_idx]), ",".join(r[i] for i in idx)) for r in rows]
    led = []
    ev_path = os.path.join(args.session, "events.jsonl")
    if os.path.exists(ev_path):
        with open(ev_path) as f:
            led = [(e["t"], e["state"]) for e in map(json.loads, filter(str.strip, f)) if e["type"] == "led"]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Replaying {len(samples)} samples ({(samples[-1][0] - samples[0][0]):.1f} s) "
          f"to {args.host}:{args.port} at {args.speed}x. Ctrl+C to stop.")
    try:
        while True:
            t0, start = samples[0][0], time.time()
            pending_led = list(led)
            for i in range(0, len(samples), BATCH):
                batch = samples[i:i + BATCH]
                due = start + (batch[-1][0] - t0) / args.speed
                time.sleep(max(0.0, due - time.time()))
                while pending_led and pending_led[0][0] <= batch[-1][0]:
                    sock.sendto(f"LED,{pending_led.pop(0)[1]}\n".encode(), (args.host, args.port))
                sock.sendto(("\n".join(line for _, line in batch) + "\n").encode(), (args.host, args.port))
            if not args.loop:
                break
            print("Looping...")
    except KeyboardInterrupt:
        pass
    print("Replay finished.")


if __name__ == "__main__":
    main()
