"""Train the IMU behaviour classifier on the Vehkaoja et al. 2022 "Movement
Sensor Dataset for Dog Behavior Classification" (Mendeley DOI
10.17632/vxhx934tbn, v4, CC BY 4.0).

    .venv/bin/python scripts/train_imu.py

Reads data/imu/csv/DogMoveData.csv (10,611,068 rows x 20 cols, ActiGraph GT9X
3D accel + 3D gyro sampled at 100 Hz on the dog's *neck* and *back*). We use
the NECK sensor channels only (ANeck_x/y/z, GNeck_x/y/z) since tomorrow's
hardware is a neck collar (MPU6050), not a harness -- see docs/RESULTS.md for
the exact unit/rate assumptions the live pipeline must match.

We use the `Behavior_1` column, which already matches our label vocabulary
almost 1:1 ("Lying chest" -> lying_on_chest, "Sniffing" -> sniffing, etc.),
filtered to contiguous same-label runs per (DogID, TestNum) session, windowed
at 2s/100 samples with a 1s/50-sample hop, and featurized with the shared
services.detection.imu_features.compute_features so training and live
inference (app/io/inputs.py::ImuStreamSource) never drift apart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.detection.imu_features import FEATURE_NAMES, compute_features  # noqa: E402

CSV_PATH = ROOT / "data" / "imu" / "csv" / "DogMoveData.csv"
MODEL_OUT = ROOT / "models" / "imu_clf.joblib"
RESULTS_OUT = ROOT / "docs" / "RESULTS.md"

SAMPLE_RATE_HZ = 100.0
WINDOW_S = 2.0
HOP_S = 1.0
WINDOW_N = int(WINDOW_S * SAMPLE_RATE_HZ)   # 200
HOP_N = int(HOP_S * SAMPLE_RATE_HZ)          # 100

LABEL_MAP = {
    "Lying chest": "lying_on_chest",
    "Sniffing": "sniffing",
    "Walking": "walking",
    "Trotting": "trotting",
    "Sitting": "sitting",
    "Standing": "standing",
    "Galloping": "galloping",
}
COLS = ["DogID", "TestNum", "t_sec", "ANeck_x", "ANeck_y", "ANeck_z", "GNeck_x", "GNeck_y", "GNeck_z", "Behavior_1"]


def build_windows():
    print(f"Reading {CSV_PATH} (this is a big file, ~440MB, may take a minute)...")
    dtypes = {c: "float32" for c in ["t_sec", "ANeck_x", "ANeck_y", "ANeck_z", "GNeck_x", "GNeck_y", "GNeck_z"]}
    dtypes["DogID"] = "int32"
    dtypes["TestNum"] = "int32"
    df = pd.read_csv(CSV_PATH, usecols=COLS, dtype=dtypes, na_values=["<undefined>"])
    df["Behavior_1"] = df["Behavior_1"].map(LABEL_MAP)
    df = df[df["Behavior_1"].notna()].reset_index(drop=True)
    print(f"Rows with a usable Behavior_1 label: {len(df)}")

    X, y, groups = [], [], []
    n_windows_per_label = {}
    for (dog_id, test_num), sess in df.groupby(["DogID", "TestNum"], sort=False):
        sess = sess.sort_values("t_sec")
        labels = sess["Behavior_1"].to_numpy()
        accel_gyro = sess[["ANeck_x", "ANeck_y", "ANeck_z", "GNeck_x", "GNeck_y", "GNeck_z"]].to_numpy()
        # split into contiguous runs of the same label
        run_start = 0
        for i in range(1, len(labels) + 1):
            if i == len(labels) or labels[i] != labels[run_start]:
                run = accel_gyro[run_start:i]
                label = labels[run_start]
                for w in _windows(run, WINDOW_N, HOP_N):
                    X.append(compute_features(w))
                    y.append(label)
                    groups.append(dog_id)
                    n_windows_per_label[label] = n_windows_per_label.get(label, 0) + 1
                run_start = i
    print("Windows per label:", n_windows_per_label)
    return np.array(X), np.array(y), np.array(groups)


def _windows(arr, window_n, hop_n):
    n = len(arr)
    if n < window_n:
        return
    pos = 0
    while pos + window_n <= n:
        yield arr[pos:pos + window_n]
        pos += hop_n


def main():
    X, y, groups = build_windows()
    if len(X) == 0:
        print("No windows built -- check the CSV path/columns.")
        sys.exit(1)

    # group-aware split: hold out ~20% of dogs entirely for testing
    unique_dogs = sorted(set(groups.tolist()))
    rng = np.random.RandomState(42)
    rng.shuffle(unique_dogs)
    n_test_dogs = max(1, int(0.2 * len(unique_dogs)))
    test_dogs = set(unique_dogs[:n_test_dogs])
    test_mask = np.array([g in test_dogs for g in groups])

    X_train, X_test = X[~test_mask], X[test_mask]
    y_train, y_test = y[~test_mask], y[test_mask]
    print(f"Train windows: {len(X_train)}, test windows: {len(X_test)}, test dogs: {sorted(test_dogs)}")

    clf = RandomForestClassifier(n_estimators=200, max_depth=16, class_weight="balanced_subsample",
                                  random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    report = classification_report(y_test, y_pred, zero_division=0)
    print(report)

    labels_sorted = sorted(set(y.tolist()))
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": clf,
        "feature_names": FEATURE_NAMES,
        "window_s": WINDOW_S,
        "hop_s": HOP_S,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "labels": labels_sorted,
        "sensor_placement": "neck",
        "source_units": "accel: g (ActiGraph GT9X); gyro: deg/s (ActiGraph GT9X)",
    }, MODEL_OUT)
    print(f"Saved {MODEL_OUT}")

    _append_results_md(report, len(X_train), len(X_test), test_dogs, labels_sorted)


def _append_results_md(report: str, n_train: int, n_test: int, test_dogs, labels_sorted):
    section = f"""
## IMU classifier

Trained on the real Vehkaoja et al. 2022 dataset (Mendeley DOI 10.17632/vxhx934tbn, v4,
CC BY 4.0), NOT synthetic -- `data/imu/csv/DogMoveData.csv`, 10,611,068 rows, ActiGraph GT9X
3D accelerometer + 3D gyroscope sampled at **100 Hz**, one sensor on the collar (neck) and one
on a harness (back). We trained on the **neck** sensor channels only (`ANeck_*`, `GNeck_*`)
since tomorrow's hardware is a neck-mounted MPU6050 collar, not a harness.

Dataset includes 3 Labrador Retrievers among its ~45 dogs (DogIDs 23, 48, 63; see
`data/imu/DogInfo.csv`), so this is genuinely breed-relevant, not just breed-agnostic.

Labels come from the `Behavior_1` column, filtered to contiguous same-label runs per
(DogID, TestNum) session and mapped: `Lying chest`->`lying_on_chest`, `Sniffing`->`sniffing`,
`Walking`->`walking`, `Trotting`->`trotting`, `Sitting`->`sitting`, `Standing`->`standing`,
`Galloping`->`galloping`. Windows: **2.0s (200 samples) with a 1.0s (100-sample) hop**.
Features (`services/detection/imu_features.py::FEATURE_NAMES`, {len(labels_sorted)} labels,
one row per window): per-axis (ax, ay, az, gx, gy, gz) mean/std/min/max/energy, plus
accel-magnitude and gyro-magnitude mean/std -- 34 features total.

Model: `RandomForestClassifier(n_estimators=200, max_depth=16, class_weight="balanced_subsample")`
(scikit-learn). Split: **group-aware by DogID** (test dogs held out entirely, not just test
rows) -- test dogs: `{sorted(test_dogs)}`. Train windows: {n_train}, test windows: {n_test}.

**Hardware note for tomorrow:** an MPU6050 read via the usual Arduino/ESP32 libraries reports
accel in *g* (or raw LSB, scaled by the library's sensitivity setting -- typically +/-2g range
=> divide raw by 16384 to get g) and gyro in deg/s (raw LSB / 131 for the +/-250 deg/s range).
This model was trained on the ActiGraph's accel-in-g / gyro-in-deg/s convention at 100 Hz, so:
(1) resample the MPU6050 stream to 100 Hz (its native rate is often ~1kHz+ or whatever the
firmware polls at -- decimate/interpolate to match), and (2) make sure the accel is in g and
gyro is in deg/s (not raw LSB, and not rad/s) before calling
`services.detection.imu_features.compute_features`. If the ActiGraph's exact g/deg-s scaling
turns out to differ once cross-checked against the real collar readings at the venue, a quick
per-axis linear rescale (fit on a few seconds of "standing still" data from both sensors) is
the fastest fix -- don't re-train from scratch under time pressure.

Per-class report (held-out dogs):

```
{report}
```
"""
    with open(RESULTS_OUT, "a") as f:
        f.write(section)
    print(f"Appended results to {RESULTS_OUT}")


if __name__ == "__main__":
    main()
