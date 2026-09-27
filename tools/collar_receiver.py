"""Receive the collar's UDP stream, show a live readout, and save it to files for the software track.

Usage (run on Windows / native OS, not inside WSL):
    python tools/collar_receiver.py                      # saves to sessions/<date_time>/
    python tools/collar_receiver.py --out sessions/dog1  # choose the folder

Files written (all updated live, safe to read while the receiver runs):
    <out>/imu.csv        every sample, 50 per second (t_laptop + the collar's fields)
    <out>/events.jsonl   one JSON object per line: marker (button A), loud, collar_connected, collar_lost
    <out>/live.json      latest sample + tilt/magnitude, rewritten ~10x per second
    sessions/current.txt path of the folder currently being written

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


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    try:
        os.replace(tmp, path)  # readers never see a half-written file
    except PermissionError:
        pass  # Windows: a reader has it open right now; next update will succeed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="folder to save into (default: sessions/<date_time>)")
    args = ap.parse_args()

    out_dir = args.out or os.path.join("sessions", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs("sessions", exist_ok=True)
    with open(os.path.join("sessions", "current.txt"), "w") as f:
        f.write(os.path.abspath(out_dir))
    imu_path = os.path.join(out_dir, "imu.csv")
    live_path = os.path.join(out_dir, "live.json")
    imu = open(imu_path, "a", buffering=1)  # line-buffered: nothing lost if killed
    if imu.tell() == 0:
        imu.write("t_laptop," + ",".join(FIELDS) + "\n")
    events = open(os.path.join(out_dir, "events.jsonl"), "a", buffering=1)

    def log_event(kind, **extra):
        events.write(json.dumps({"t": round(time.time(), 3), "type": kind, **extra}) + "\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", DATA_PORT))
    sock.settimeout(1.0)
    print(f"Listening on UDP {DATA_PORT}. Saving to {os.path.abspath(out_dir)}")
    print("Waiting for collar...")

    collar = {"ip": None}

    def command_loop():
        while True:
            try:
                cmd = input().strip()
            except EOFError:
                return
            if cmd and collar["ip"]:
                sock.sendto(cmd.upper().encode(), (collar["ip"], CMD_PORT))
                print(f"-> sent {cmd.upper()} to {collar['ip']}")

    threading.Thread(target=command_loop, daemon=True).start()

    count, last_print, last_live, last_seq, dropped = 0, time.time(), 0.0, None, 0
    btn_prev = 0
    last_loud = 0.0
    connected = False
    rate = 0.0
    while True:
        try:
            data, addr = sock.recvfrom(512)
        except socket.timeout:
            print("  (no data for 1 s - is the collar on and LAPTOP_IP correct?)")
            if connected:
                log_event("collar_lost")
                connected = False
            continue
        if collar["ip"] != addr[0] or not connected:
            collar["ip"] = addr[0]
            connected = True
            log_event("collar_connected", ip=addr[0])
            print(f"Collar connected from {addr[0]}")

        now = time.time()
        line = data.decode().strip()
        parts = line.split(",")
        if len(parts) != len(FIELDS):
            continue
        s = dict(zip(FIELDS, parts))
        seq = int(s["seq"])
        if last_seq is not None and seq > last_seq + 1:
            dropped += seq - last_seq - 1
        last_seq = seq
        count += 1
        imu.write(f"{now:.3f},{line}\n")

        if int(s["btnA"]) and not btn_prev:
            print(f"*** EVENT MARKER at {time.strftime('%H:%M:%S')} ***")
            log_event("marker")
        btn_prev = int(s["btnA"])

        # Loud sound at the collar = very likely the dog itself (bark/whine), not the TV
        if int(s["loud"]) and now - last_loud > 1.0:
            print(f"🔊 LOUD at collar {time.strftime('%H:%M:%S')} (mic={s['mic']})")
            log_event("loud", mic=int(s["mic"]))
            last_loud = now

        ax, ay, az = float(s["ax"]), float(s["ay"]), float(s["az"])
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, az))

        if now - last_live >= 0.1:
            write_json_atomic(live_path, {
                "t_laptop": round(now, 3), "seq": seq,
                "ax": ax, "ay": ay, "az": az,
                "gx": float(s["gx"]), "gy": float(s["gy"]), "gz": float(s["gz"]),
                "mag": round(mag, 3), "pitch": round(pitch, 1), "roll": round(roll, 1),
                "mic": int(s["mic"]), "loud": int(s["loud"]),
                "btnA": int(s["btnA"]), "btnB": int(s["btnB"]),
                "rate_hz": round(rate, 1), "dropped": dropped,
                "collar_ip": collar["ip"], "cmd_port": CMD_PORT,
            })
            last_live = now

        if now - last_print >= 0.5:
            rate = count / (now - last_print)
            print(f"{rate:5.1f} Hz | |a|={mag:4.2f} g  pitch={pitch:6.1f}  roll={roll:6.1f} | "
                  f"gyro=({s['gx']},{s['gy']},{s['gz']}) | mic={s['mic']:>4} {'#' * min(int(s['mic']) // 20, 20):<20} | "
                  f"A={s['btnA']} B={s['btnB']} | dropped={dropped}")
            count, last_print = 0, now


if __name__ == "__main__":
    main()
