"""Canonical IMU window -> feature-vector function, shared by training (scripts/train_imu.py)
and live inference (app/io/inputs.py::ImuStreamSource) so the two never drift apart.

A "sample" is a 6-tuple/list (ax, ay, az, gx, gy, gz). Units: whatever the training data used
(document this in docs/RESULTS.md) -- callers are responsible for resampling/rescaling a real
sensor (e.g. MPU6050) to match before calling compute_features.
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

import numpy as np

AXES = ["ax", "ay", "az", "gx", "gy", "gz"]
STATS = ["mean", "std", "min", "max", "energy"]
FEATURE_NAMES: List[str] = [f"{axis}_{stat}" for axis in AXES for stat in STATS] + [
    "accel_mag_mean", "accel_mag_std", "gyro_mag_mean", "gyro_mag_std",
]


def compute_features(window: Sequence[Sequence[float]]) -> List[float]:
    """window: iterable of 6-tuples (ax,ay,az,gx,gy,gz). Returns a vector matching FEATURE_NAMES."""
    arr = np.asarray(window, dtype=float)  # (n_samples, 6)
    if arr.ndim != 2 or arr.shape[1] != 6:
        raise ValueError(f"expected (n, 6) window, got shape {arr.shape}")
    feats: List[float] = []
    for col in range(6):
        v = arr[:, col]
        feats.extend([float(v.mean()), float(v.std()), float(v.min()), float(v.max()), float(np.sum(v ** 2))])
    accel_mag = np.linalg.norm(arr[:, 0:3], axis=1)
    gyro_mag = np.linalg.norm(arr[:, 3:6], axis=1)
    feats.extend([float(accel_mag.mean()), float(accel_mag.std()), float(gyro_mag.mean()), float(gyro_mag.std())])
    return feats


def windows(samples: Iterable[Sequence[float]], window_n: int, hop_n: int):
    """Yield successive overlapping windows (as lists) of length window_n from a flat sample stream."""
    buf: List[Sequence[float]] = []
    for s in samples:
        buf.append(s)
        if len(buf) >= window_n:
            yield list(buf[-window_n:])
            buf = buf[hop_n:]
