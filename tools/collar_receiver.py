"""Receive the collar's UDP stream, show a live readout, and save it to files for the software track.

Usage (run on Windows / native OS, not inside WSL), from any folder:
    python tools/collar_receiver.py                      # saves to <project>/sessions/<date_time>/
    python tools/collar_receiver.py --out sessions/dog1  # choose the folder

Files written (all updated live, safe to read while the receiver runs):
    <out>/imu.csv        every sample, 50 per second (t_laptop + the collar's fields)
    <out>/events.jsonl   one JSON object per line: marker (button A), loud, collar_connected, collar_lost
    <out>/live.json      latest sample + tilt/magnitude, rewritten ~10x per second
    <project>/sessions/current.txt   path of the folder currently being written

While running, type a command and press Enter to drive the collar LED:
    calm | attn | alert | off | led 0 0 255
"""
import argparse
import json
import math
import os
import socket
import threading
import time

DATA_PORT = 4210
CMD_PORT = 4211
FIELDS = "seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,loud".split(",")
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions")


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    try:
        os.replace(tmp, path)  # readers never see a half-written file
    except PermissionError:
        pass  # Windows: a reader has it open right now; next update will succeed


class Receiver:
    def __init__(self, out_dir):
        self.imu = open(os.path.join(out_dir, "imu.csv"), "a", buffering=1)  # line-buffered: nothing lost if killed
        if self.imu.tell() == 0:
            self.imu.write("t_laptop," + ",".join(FIELDS) + "\n")
        self.events = open(os.path.join(out_dir, "events.jsonl"), "a", buffering=1)
        self.live_path = os.path.join(out_dir, "live.json")
        self.collar_ip = None
        self.count, self.last_print, self.last_live = 0, time.time(), 0.0
        self.last_seq, self.dropped, self.rate = None, 0, 0.0
        self.btn_prev, self.last_loud = 0, 0.0

    def log_event(self, kind, t=None, **extra):
        self.events.write(json.dumps({"t": round(t or time.time(), 3), "type": kind, **extra}) + "\n")

    def handle_packet(self, data):
        # One packet holds several samples (one CSV line each). Spread their laptop timestamps
        # back from the arrival time using the collar's own millisecond clock.
        arrived = time.time()
        rows = [line.split(",") for line in data.decode(errors="ignore").strip().splitlines()]
        rows = [r for r in rows if len(r) == len(FIELDS)]
        if not rows:
            return
        last_ms = int(rows[-1][1])
        for parts in rows:
            self.handle_sample(parts, arrived - (last_ms - int(parts[1])) / 1000.0)

    def handle_sample(self, parts, now):
        s = dict(zip(FIELDS, parts))
        seq = int(s["seq"])
        if self.last_seq is not None and seq > self.last_seq + 1:
            self.dropped += seq - self.last_seq - 1
        self.last_seq = seq
        self.count += 1
        self.imu.write(f"{now:.3f}," + ",".join(parts) + "\n")

        if int(s["btnA"]) and not self.btn_prev:
            print(f"*** EVENT MARKER at {time.strftime('%H:%M:%S')} ***")
            self.log_event("marker", now)
        self.btn_prev = int(s["btnA"])

        # Loud sound at the collar = very likely the dog itself (bark/whine), not the TV
        if int(s["loud"]) and now - self.last_loud > 1.0:
            print(f"🔊 LOUD at collar {time.strftime('%H:%M:%S')} (mic={s['mic']})")
            self.log_event("loud", now, mic=int(s["mic"]))
            self.last_loud = now

        ax, ay, az = float(s["ax"]), float(s["ay"]), float(s["az"])
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, az))

        wall = time.time()
        if wall - self.last_live >= 0.1:
            write_json_atomic(self.live_path, {
                "t_laptop": round(now, 3), "seq": seq,
                "ax": ax, "ay": ay, "az": az,
                "gx": float(s["gx"]), "gy": float(s["gy"]), "gz": float(s["gz"]),
                "mag": round(mag, 3), "pitch": round(pitch, 1), "roll": round(roll, 1),
                "mic": int(s["mic"]), "loud": int(s["loud"]),
                "btnA": int(s["btnA"]), "btnB": int(s["btnB"]),
                "rate_hz": round(self.rate, 1), "dropped": self.dropped,
                "collar_ip": self.collar_ip, "cmd_port": CMD_PORT,
            })
            self.last_live = wall

        if wall - self.last_print >= 0.5:
            self.rate = self.count / (wall - self.last_print)
            print(f"{self.rate:5.1f} Hz | |a|={mag:4.2f} g  pitch={pitch:6.1f}  roll={roll:6.1f} | "
                  f"gyro=({s['gx']},{s['gy']},{s['gz']}) | mic={s['mic']:>4} {'#' * min(int(s['mic']) // 20, 20):<20} | "
                  f"A={s['btnA']} B={s['btnB']} | dropped={self.dropped}")
            self.count, self.last_print = 0, wall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="folder to save into (default: <project>/sessions/<date_time>)")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out or os.path.join(SESSIONS_DIR, time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(os.path.join(SESSIONS_DIR, "current.txt"), "w") as f:
        f.write(out_dir)
    rx = Receiver(out_dir)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)  # big buffer: no loss if we stall briefly
    sock.bind(("0.0.0.0", DATA_PORT))
    sock.settimeout(1.0)
    print(f"Listening on UDP {DATA_PORT}. Saving to {out_dir}")
    print("Waiting for collar...")

    def command_loop():
        while True:
            try:
                cmd = input().strip()
            except EOFError:
                return
            if cmd and rx.collar_ip:
                sock.sendto(cmd.upper().encode(), (rx.collar_ip, CMD_PORT))
                print(f"-> sent {cmd.upper()} to {rx.collar_ip}")

    threading.Thread(target=command_loop, daemon=True).start()

    connected = False
    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            print("  (no data for 1 s - is the collar on and LAPTOP_IP correct?)")
            if connected:
                rx.log_event("collar_lost")
                connected = False
            continue
        if not connected:
            print(f"Collar {'connected' if rx.collar_ip is None else 'data resumed'} from {addr[0]}")
            rx.log_event("collar_connected", ip=addr[0])
            rx.collar_ip = addr[0]
            connected = True
        rx.handle_packet(data)


if __name__ == "__main__":
    main()
