#!/usr/bin/env bash
# Reproduces every dataset/model download used by this prototype. Idempotent:
# safe to re-run, skips anything already present. Each step has a network
# timeout so a blocked/slow host doesn't hang the whole script.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY=python3

mkdir -p data/imu data/audio_samples data/demo_videos models

# --- 1. Vehkaoja et al. 2022 IMU dataset (Mendeley, CC BY 4.0) --------------
if [ ! -f data/imu/csv/DogMoveData.csv ]; then
  echo "[download_data] fetching IMU dataset (~440MB, budget ~5 min)..."
  curl -s -L --max-time 300 -o data/imu/DogMoveData_csv_format.zip \
    "https://data.mendeley.com/public-files/datasets/vxhx934tbn/files/52f9412d-799b-4ff5-86b7-4e6576276cb9/file_downloaded" \
    && (cd data/imu && unzip -o -q DogMoveData_csv_format.zip -d csv) \
    && echo "[download_data] IMU dataset ready" \
    || echo "[download_data] IMU dataset download FAILED or timed out -- see scripts/train_imu.py fallback notes"
else
  echo "[download_data] IMU dataset already present, skipping"
fi
[ -f data/imu/Data_description.txt ] || curl -s --max-time 20 -L -o data/imu/Data_description.txt \
  "https://data.mendeley.com/public-files/datasets/vxhx934tbn/files/237b11c5-6175-4f1f-a1be-74509c699eb8/file_downloaded"
[ -f data/imu/DogInfo.csv ] || curl -s --max-time 20 -L -o data/imu/DogInfo.csv \
  "https://data.mendeley.com/public-files/datasets/vxhx934tbn/files/7c35314a-016b-46c7-bcd2-daf6e0cec1bb/file_downloaded"

# --- 2. YOLO COCO-pretrained weights (dog = class 16) -----------------------
if [ ! -f models/yolo11n.pt ]; then
  echo "[download_data] fetching yolo11n.pt..."
  "$PY" - <<'PY'
from ultralytics import YOLO
import shutil, os
m = YOLO("yolo11n.pt")
assert m.names[16] == "dog"
if os.path.exists("yolo11n.pt"):
    shutil.copy("yolo11n.pt", "models/yolo11n.pt")
PY
else
  echo "[download_data] models/yolo11n.pt already present, skipping"
fi

# --- 3. ESC-50 dog_bark sanity-check clips (direct raw files, not full clone)
if [ ! -f data/audio_samples/1-100032-A-0.wav ]; then
  echo "[download_data] fetching ESC-50 dog_bark sample clips..."
  for f in 1-100032-A-0.wav 1-110389-A-0.wav 1-30226-A-0.wav; do
    curl -s --max-time 20 -o "data/audio_samples/$f" \
      "https://raw.githubusercontent.com/karolpiczak/ESC-50/master/audio/$f"
  done
else
  echo "[download_data] ESC-50 clips already present, skipping"
fi

# --- 4. Demo footage: scripted download is blocked (Pixabay/Pexels 403 bot
#        protection) -- see docs/DEMO_FOOTAGE.md for the manual steps.
if [ -z "$(ls -A data/demo_videos 2>/dev/null)" ]; then
  echo "[download_data] data/demo_videos is empty -- see docs/DEMO_FOOTAGE.md (manual download needed, or use the webcam)"
fi

echo "[download_data] done. Now run: .venv/bin/python scripts/train_imu.py"
