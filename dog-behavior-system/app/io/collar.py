"""Reads the PawPatrol collar's session folder -- the files its receiver writes -- and turns the
IMU stream into movement labels. No code is shared with the hardware project; the file format
(docs/COLLAR_DATA.md in paw-patrol) is the contract:

    <session>/imu.csv        t_laptop,seq,ms,ax,ay,az,gx,gy,gz,mic,btnA,btnB,loud   (50 Hz)
    <session>/events.jsonl   {"t", "type": marker|loud|led|collar_connected|collar_lost, ...}
    <session>/live.json      latest reading incl. collar_ip / cmd_port (for the LED)
    <sessions>/current.txt   path of the session being written

`t_laptop` / `t` are laptop Unix seconds: the sync key with our webcam/mic recordings. If the
receiver ran on another machine, pass `offset_s` (added to every collar timestamp).
"""
from __future__ import annotations

import bisect
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from app.config import get as cfg_get

RATE_HZ = 50
_INT = {"seq", "ms", "mic", "btnA", "btnB", "loud"}


# ---------------------------------------------------------------------------------------- files
def resolve_session(path: str) -> Path:
    """Accepts a session folder, or a sessions dir (then follows current.txt). current.txt may hold
    a path from another machine (e.g. Windows) -- then its folder name is looked up locally."""
    p = Path(path).expanduser().resolve()
    if (p / "imu.csv").exists():
        return p
    cur = p / "current.txt"
    if cur.exists():
        target = cur.read_text().strip()
        for cand in (Path(target), p / Path(target.replace("\\", "/")).name):
            if (cand / "imu.csv").exists() or cand.is_dir():
                return cand
    raise FileNotFoundError(f"no collar session at {p} (expected imu.csv or current.txt)")


def _parse(header: List[str], line: str) -> Optional[dict]:
    parts = line.strip().split(",")
    if len(parts) != len(header):
        return None
    try:
        return {k: (int(v) if k in _INT else float(v)) for k, v in zip(header, parts)}
    except ValueError:
        return None


def load_imu(session: Path) -> List[dict]:
    with open(Path(session) / "imu.csv") as f:
        header = f.readline().strip().split(",")
        return [r for r in (_parse(header, l) for l in f) if r]


def load_events(session: Path) -> List[dict]:
    path = Path(session) / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def read_live(session: Path) -> Optional[dict]:
    try:
        return json.loads((Path(session) / "live.json").read_text())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------------------------- features
def _cfg(key: str, default):
    return cfg_get("collar", "gait", key, default=default)


def calibrate(rows: List[dict]) -> dict:
    """Resting reference (this sensor reads ~1.2 g at rest and has gyro bias). Uses the stillest
    3 s within the first 30 s, so the recording doesn't need to start perfectly still."""
    n = 3 * RATE_HZ
    head = rows[: 30 * RATE_HZ]
    if len(head) < n:
        head, n = rows, max(1, len(rows))
    a = np.array([[r["ax"], r["ay"], r["az"]] for r in head])
    g = np.array([[r["gx"], r["gy"], r["gz"]] for r in head])
    mag = np.linalg.norm(a, axis=1)
    best = min(range(0, max(1, len(head) - n + 1), RATE_HZ // 2), key=lambda i: mag[i:i + n].std())
    return {"g": float(mag[best:best + n].mean()) or 1.0, "gyro_bias": g[best:best + n].mean(axis=0)}


def window_features(win: List[dict], cal: dict) -> dict:
    a = np.array([[r["ax"], r["ay"], r["az"]] for r in win])
    gy = np.array([[r["gx"], r["gy"], r["gz"]] for r in win]) - cal["gyro_bias"]
    mag = np.linalg.norm(a, axis=1) / cal["g"]
    spec = np.abs(np.fft.rfft(mag - mag.mean()))
    freqs = np.fft.rfftfreq(len(mag), 1 / RATE_HZ)
    band = (freqs >= 0.5) & (freqs <= 8.0)
    cadence = float(freqs[band][spec[band].argmax()]) if band.any() else 0.0
    return {
        "t_start": win[0]["t_laptop"], "t_end": win[-1]["t_laptop"],
        "mag_std": float(mag.std()), "mag_max": float(mag.max()),
        "gyro_mean_dps": float(np.linalg.norm(gy, axis=1).mean()), "cadence_hz": cadence,
        "mic_max": int(max(r["mic"] for r in win)), "loud": any(r["loud"] for r in win),
        "marker": any(r["btnA"] for r in win),
    }


def classify(f: dict) -> str:
    """still | walking | trotting | galloping (rule-of-thumb thresholds from COLLAR_DATA.md §7;
    tune in config.yaml -> collar.gait on the real dog)."""
    if f["mag_std"] < _cfg("still_mag_std", 0.02) and f["gyro_mean_dps"] < _cfg("still_gyro_dps", 5.0):
        return "still"
    if f["mag_std"] < _cfg("moving_mag_std", 0.03):
        return "still"  # small fidgets / head turns: not travelling
    if f["mag_max"] > _cfg("gallop_mag_max", 2.0) or f["cadence_hz"] >= _cfg("trot_max_cadence", 3.0):
        return "galloping"
    if f["cadence_hz"] >= _cfg("walk_max_cadence", 2.0):
        return "trotting"
    return "walking"


# ------------------------------------------------------------------------------------- timeline
class CollarTimeline:
    """Per-window collar readings with time lookups. Works offline (load) or live (follow).
    All times returned/accepted are in OUR clock: collar time + offset_s."""

    def __init__(self, offset_s: float = 0.0, window_s: float = 2.0, step_s: float = 1.0):
        self.offset_s = offset_s
        self.win_n, self.step_n = int(window_s * RATE_HZ), int(step_s * RATE_HZ)
        self.windows: List[dict] = []    # features + "activity", sorted by t_start
        self._starts: List[float] = []
        self.events: List[dict] = []     # events.jsonl entries (t shifted)
        self.cal: Optional[dict] = None
        self.session: Optional[Path] = None
        self._lock = threading.Lock()
        self.last_sample_t = 0.0
        self.adaptive = False  # live follow: keep improving the calibration
        self._still_std = float("inf")

    # -- building
    def _add_windows(self, rows: List[dict]) -> None:
        for i in range(0, len(rows) - self.win_n + 1, self.step_n):
            win = rows[i:i + self.win_n]
            if self.adaptive:
                # live: (re)calibrate on the stillest window seen so far, so the dog doesn't have
                # to stand still at the exact moment we start following the files
                raw_std = float(np.linalg.norm([[r["ax"], r["ay"], r["az"]] for r in win], axis=1).std())
                if self.cal is None or raw_std < self._still_std:
                    self.cal, self._still_std = calibrate(win), raw_std
            f = window_features(win, self.cal)
            f["t_start"] += self.offset_s
            f["t_end"] += self.offset_s
            f["activity"] = classify(f)
            with self._lock:
                self.windows.append(f)
                self._starts.append(f["t_start"])

    @classmethod
    def load(cls, session: str, offset_s: float = 0.0) -> "CollarTimeline":
        tl = cls(offset_s)
        tl.session = resolve_session(session)
        rows = load_imu(tl.session)
        if rows:
            tl.cal = calibrate(rows)  # offline: the stillest 3 s near the start
        tl._add_windows(rows)
        tl.events = [{**e, "t": e["t"] + offset_s} for e in load_events(tl.session) if "t" in e]
        if rows:
            tl.last_sample_t = rows[-1]["t_laptop"] + offset_s
        return tl

    def follow(self, session: str) -> "CollarTimeline":
        """Tail the live session files in a background thread."""
        self.session = resolve_session(session)
        self.adaptive = True
        threading.Thread(target=self._follow_loop, daemon=True).start()
        return self

    def _follow_loop(self) -> None:
        path = self.session / "imu.csv"
        while not path.exists():
            time.sleep(0.5)
        pending: List[dict] = []
        n_events_seen = 0
        with open(path) as f:
            header = f.readline().strip().split(",")
            f.seek(0, os.SEEK_END)  # live: only new samples
            buf = ""
            while True:
                line = f.readline()
                if line:
                    buf += line
                    if not buf.endswith("\n"):
                        continue
                    row = _parse(header, buf)
                    buf = ""
                    if row:
                        pending.append(row)
                        self.last_sample_t = row["t_laptop"] + self.offset_s
                    if len(pending) >= self.win_n:
                        self._add_windows(pending[: self.win_n])
                        pending = pending[self.step_n:]
                    continue
                evs = load_events(self.session)  # small file: re-read for new events
                if len(evs) > n_events_seen:
                    with self._lock:
                        self.events.extend({**e, "t": e["t"] + self.offset_s} for e in evs[n_events_seen:] if "t" in e)
                    n_events_seen = len(evs)
                time.sleep(0.05)

    # -- queries (our clock)
    def connected(self, t: Optional[float] = None) -> bool:
        t = time.time() if t is None else t
        return bool(self.windows) and self.windows[0]["t_start"] - 1 <= t <= self.last_sample_t + 2.0

    def window_at(self, t: float) -> Optional[dict]:
        with self._lock:
            i = bisect.bisect_right(self._starts, t) - 1
            if i < 0:
                return None
            w = self.windows[i]
        return w if w["t_start"] <= t <= w["t_end"] + 1.0 else None

    def activity_at(self, t: float) -> Optional[str]:
        w = self.window_at(t)
        return w["activity"] if w else None

    def mic_max_between(self, t0: float, t1: float) -> Optional[int]:
        with self._lock:
            ws = [w for w in self.windows if w["t_end"] >= t0 and w["t_start"] <= t1]
        return max((w["mic_max"] for w in ws), default=None) if ws else None

    def segments(self) -> List[dict]:
        """Consecutive equal activities merged: [{activity, t_start, t_end}]."""
        segs: List[dict] = []
        with self._lock:
            for w in self.windows:
                if segs and segs[-1]["activity"] == w["activity"] and w["t_start"] <= segs[-1]["t_end"] + 1.5:
                    segs[-1]["t_end"] = w["t_end"]
                else:
                    segs.append({"activity": w["activity"], "t_start": w["t_start"], "t_end": w["t_end"]})
        return segs


def mic_envelope(rows: List[dict]) -> np.ndarray:
    """(N, 2) array of (t_laptop, mic level) -- for clap-based clock sync."""
    return np.array([[r["t_laptop"], r["mic"]] for r in rows], dtype=float)
