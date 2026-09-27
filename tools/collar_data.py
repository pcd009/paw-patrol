"""Helpers for using PawPatrol collar data from the software side. Standard library only.

Typical use:
    from collar_data import *
    session = current_session()                  # folder the receiver is writing right now
    rows = load_imu(session)                     # list of dicts, one per sample (50 Hz)
    cal = calibrate(rows[:250])                  # first 5 s of standing still
    for win in windows(rows, seconds=2.0, step=1.0):
        print(window_features(win, cal))
    events = load_events(session)
    send_led("ALERT", session)                   # turn the collar LED red

    for row in follow_imu(session):              # live: yields new samples as they arrive
        ...

See docs/COLLAR_DATA.md for the full data format.
"""
import cmath
import json
import math
import os
import socket
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_DIR = os.path.join(PROJECT_DIR, "sessions")
SAMPLE_RATE_HZ = 50
LED_STATES = ("CALM", "ATTN", "ALERT", "OFF")
_INT_FIELDS = ("seq", "ms", "mic", "btnA", "btnB", "loud")


def current_session():
    """Path of the session folder the receiver is currently writing (from sessions/current.txt)."""
    with open(os.path.join(SESSIONS_DIR, "current.txt")) as f:
        return f.read().strip()


def _parse_row(header, line):
    parts = line.strip().split(",")
    if len(parts) != len(header):
        return None
    row = {}
    for k, v in zip(header, parts):
        row[k] = int(v) if k in _INT_FIELDS else float(v)
    return row


def load_imu(session):
    """All samples so far: list of dicts with t_laptop, seq, ms, ax..gz, mic, btnA, btnB, loud."""
    with open(os.path.join(session, "imu.csv")) as f:
        header = f.readline().strip().split(",")
        return [r for r in (_parse_row(header, line) for line in f) if r]


def follow_imu(session, from_start=False, poll_s=0.05):
    """Generator yielding new samples as the receiver appends them (like `tail -f`)."""
    path = os.path.join(session, "imu.csv")
    with open(path) as f:
        header = f.readline().strip().split(",")
        if not from_start:
            f.seek(0, os.SEEK_END)
        buf = ""
        while True:
            chunk = f.readline()
            if not chunk:
                time.sleep(poll_s)
                continue
            buf += chunk
            if not buf.endswith("\n"):
                continue  # partial line: wait for the rest
            row = _parse_row(header, buf)
            buf = ""
            if row:
                yield row


def load_events(session, kind=None):
    """Events from events.jsonl (marker, loud, led, collar_connected, collar_lost), optionally one kind."""
    path = os.path.join(session, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    return [e for e in events if kind is None or e["type"] == kind]


def read_live(session):
    """Latest reading (live.json), or None if it isn't there yet / is mid-replace."""
    try:
        with open(os.path.join(session, "live.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def send_led(state, session=None, collar_ip=None, port=4211):
    """Set the collar LED: "CALM" (green), "ATTN" (amber), "ALERT" (red), "OFF", or (r, g, b) 0-255.
    The collar IP is taken from live.json unless given. The collar reports the change back, so it is
    logged as an `led` event automatically."""
    if collar_ip is None:
        live = read_live(session or current_session())
        if not live or not live.get("collar_ip"):
            raise RuntimeError("collar IP unknown: is the receiver running and the collar connected?")
        collar_ip, port = live["collar_ip"], live.get("cmd_port", port)
    msg = f"LED {state[0]} {state[1]} {state[2]}" if isinstance(state, (tuple, list)) else str(state).upper()
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(msg.encode(), (collar_ip, port))


def magnitude(r):
    return math.sqrt(r["ax"] ** 2 + r["ay"] ** 2 + r["az"] ** 2)


def calibrate(still_rows):
    """Resting reference from a stretch where the collar/dog is still (e.g. the first 5-10 s).
    Returns gravity magnitude (this sensor reads ~1.2 g, not 1.0), gravity direction and gyro bias."""
    n = len(still_rows)
    if n == 0:
        raise ValueError("no rows to calibrate on")
    mean = {k: sum(r[k] for r in still_rows) / n for k in ("ax", "ay", "az", "gx", "gy", "gz")}
    g = math.sqrt(mean["ax"] ** 2 + mean["ay"] ** 2 + mean["az"] ** 2)
    return {
        "g": g,
        "gravity_dir": (mean["ax"] / g, mean["ay"] / g, mean["az"] / g),
        "gyro_bias": (mean["gx"], mean["gy"], mean["gz"]),
    }


def tilt_deg(r):
    """(pitch, roll) in degrees from the accelerometer. Meaning depends on how the collar is mounted."""
    pitch = math.degrees(math.atan2(-r["ax"], math.sqrt(r["ay"] ** 2 + r["az"] ** 2)))
    roll = math.degrees(math.atan2(r["ay"], r["az"]))
    return pitch, roll


def windows(rows, seconds=2.0, step=1.0):
    """Sliding windows of samples (default 2 s long, every 1 s)."""
    n, s = int(seconds * SAMPLE_RATE_HZ), int(step * SAMPLE_RATE_HZ)
    for i in range(0, max(len(rows) - n + 1, 0), s):
        yield rows[i:i + n]


def dominant_freq(values, fmin=0.5, fmax=8.0):
    """Strongest frequency (Hz) in a signal sampled at 50 Hz, e.g. step cadence from |a|."""
    n = len(values)
    if n < 8:
        return 0.0
    mean = sum(values) / n
    x = [v - mean for v in values]
    best_f, best_p = 0.0, 0.0
    for k in range(1, n // 2):
        f = k * SAMPLE_RATE_HZ / n
        if f < fmin or f > fmax:
            continue
        p = abs(sum(x[t] * cmath.exp(-2j * math.pi * k * t / n) for t in range(n)))
        if p > best_p:
            best_f, best_p = f, p
    return best_f


def window_features(win, cal=None):
    """Explainable features for one window: the inputs for an activity classifier / rules."""
    g = cal["g"] if cal else 1.0
    bias = cal["gyro_bias"] if cal else (0.0, 0.0, 0.0)
    mags = [magnitude(r) / g for r in win]  # 1.0 = resting gravity after calibration
    gyro = [math.sqrt((r["gx"] - bias[0]) ** 2 + (r["gy"] - bias[1]) ** 2 + (r["gz"] - bias[2]) ** 2) for r in win]
    mean_mag = sum(mags) / len(mags)
    tilts = [tilt_deg(r) for r in win]
    return {
        "t_start": win[0]["t_laptop"],
        "t_end": win[-1]["t_laptop"],
        "mag_std": math.sqrt(sum((m - mean_mag) ** 2 for m in mags) / len(mags)),  # motion energy
        "mag_max": max(mags),                                                       # impacts / gallop peaks
        "gyro_mean_dps": sum(gyro) / len(gyro),                                     # turning / head movement
        "cadence_hz": dominant_freq(mags),                                          # walk ~1-2, trot ~2-3, gallop 3+
        "pitch_deg": sum(p for p, _ in tilts) / len(tilts),                        # posture (after mounting known)
        "roll_deg": sum(r for _, r in tilts) / len(tilts),
        "mic_max": max(r["mic"] for r in win),
        "loud": any(r["loud"] for r in win),
        "marker": any(r["btnA"] for r in win),
    }
