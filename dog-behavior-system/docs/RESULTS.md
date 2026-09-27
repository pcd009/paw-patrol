
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
Features (`services/detection/imu_features.py::FEATURE_NAMES`, 7 labels,
one row per window): per-axis (ax, ay, az, gx, gy, gz) mean/std/min/max/energy, plus
accel-magnitude and gyro-magnitude mean/std -- 34 features total.

Model: `RandomForestClassifier(n_estimators=200, max_depth=16, class_weight="balanced_subsample")`
(scikit-learn). Split: **group-aware by DogID** (test dogs held out entirely, not just test
rows) -- test dogs: `[21, 26, 30, 51, 52, 61, 67, 70, 73]`. Train windows: 35432, test windows: 7660.

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
                precision    recall  f1-score   support

     galloping       0.00      0.00      0.00         2
lying_on_chest       0.58      0.83      0.68      1707
       sitting       0.55      0.33      0.41      1095
      sniffing       0.99      1.00      0.99      1967
      standing       0.65      0.33      0.44       798
      trotting       0.98      0.99      0.99       942
       walking       0.93      0.99      0.96      1149

      accuracy                           0.79      7660
     macro avg       0.67      0.64      0.64      7660
  weighted avg       0.79      0.79      0.78      7660

```

## YOLO dog detection

`models/yolo11n.pt` (Ultralytics, COCO-pretrained, auto-downloaded via `ultralytics`)
confirmed programmatically: `model.names[16] == "dog"`. No fine-tuning done or needed --
COCO already detects dogs reliably. The optional Stanford Dogs Labrador-subset recall
sanity-check was skipped to prioritize the IMU classifier and the rest of the pipeline
under time pressure (the tarball is ~750MB and not on the critical path); COCO's general
dog class is used as-is. If time allows before the demo, a quick recall check is: download
a handful of Labrador photos, run `YOLO('models/yolo11n.pt')(img, classes=[16])` on each,
and count how many return at least one box.

## Audio test clips

3 real dog-bark clips pulled directly from ESC-50 (`meta/esc50.csv` category=`dog`) into
`data/audio_samples/` (`1-100032-A-0.wav`, `1-110389-A-0.wav`, `1-30226-A-0.wav`), used to
smoke-test `services/detection/audio_detector.py` against the pretrained AST model.

## Demo footage

Scripted Pixabay/Pexels download was blocked (HTTP 403, bot protection) -- see
`docs/DEMO_FOOTAGE.md` for exact manual-download instructions and the webcam fallback.
