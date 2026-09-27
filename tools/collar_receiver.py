"""Receive the collar's UDP stream, show a live readout, optionally record to CSV.

Usage (run on Windows / native OS, not inside WSL):
    python tools/collar_receiver.py                      # live readout
    python tools/collar_receiver.py --record test.csv    # also save every sample

While running, type a command and press Enter to drive the collar LED:
    calm | attn | alert | off | led 0 0 255
"""
import argparse
import math
import socket
import threading
import time

DATA_PORT = 4210
CMD_PORT = 4211
FIELDS = "seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,rec,loud".split(",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", help="CSV file to write samples to")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", DATA_PORT))
    sock.settimeout(1.0)
    print(f"Listening on UDP {DATA_PORT}. Waiting for collar...")

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

    out = open(args.record, "w", buffering=1) if args.record else None  # line-buffered: nothing lost if killed
    if out:
        out.write("t_laptop," + ",".join(FIELDS) + "\n")

    count, last_print, last_seq, dropped = 0, time.time(), None, 0
    btn_prev = 0
    last_loud = 0.0
    while True:
        try:
            data, addr = sock.recvfrom(512)
        except socket.timeout:
            print("  (no data for 1 s - is the collar on and LAPTOP_IP correct?)")
            continue
        if collar["ip"] != addr[0]:
            collar["ip"] = addr[0]
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
        if out:
            out.write(f"{now:.3f},{line}\n")

        if int(s["btnA"]) and not btn_prev:
            print(f"*** EVENT MARKER at {time.strftime('%H:%M:%S')} ***")
        btn_prev = int(s["btnA"])

        # Loud sound at the collar = very likely the dog itself (bark/whine), not the TV
        if int(s["loud"]) and now - last_loud > 1.0:
            print(f"🔊 LOUD at collar {time.strftime('%H:%M:%S')} (mic={s['mic']})")
            last_loud = now

        if now - last_print >= 0.5:
            ax, ay, az = float(s["ax"]), float(s["ay"]), float(s["az"])
            mag = math.sqrt(ax * ax + ay * ay + az * az)
            pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
            roll = math.degrees(math.atan2(ay, az))
            rate = count / (now - last_print)
            print(f"{rate:5.1f} Hz | |a|={mag:4.2f} g  pitch={pitch:6.1f}  roll={roll:6.1f} | "
                  f"gyro=({s['gx']},{s['gy']},{s['gz']}) | mic={s['mic']:>4} {'#' * min(int(s['mic']) // 20, 20):<20} | "
                  f"A={s['btnA']} B={s['btnB']} rec={s['rec']} | dropped={dropped}")
            count, last_print = 0, now


if __name__ == "__main__":
    main()
