"""Seed today's history with a realistic Labrador day (up to now), for the pitch.

    .venv/bin/python -m scripts.seed_demo_day            # refuses if today already has history
    .venv/bin/python -m scripts.seed_demo_day --replace  # wipe today's history first

Every seeded event is tagged evidence.detector = "demo_seed" and the dashboard shows a
"includes demo history" badge, so it's never mistaken for real observations. Thumbnails are
dog crops taken from the clips in data/demo_videos/. Alerts come from the real rule engine.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import cv2

from app import history
from contracts.common import SCHEMA_VERSION, to_iso
from contracts.validate import validate
from services.context_rules.engine import evaluate_rules, resolve_stale
from services.detection.video_detector import DOG_CLASS_ID, ROOT, _thumb_jpeg

# (local HH:MM, label, duration in minutes) -- a working day for a 3-year-old Lab
DAY = [
    ("06:30", "lying_on_chest", 65), ("07:35", "standing", 2), ("07:37", "walking", 3),
    ("07:40", "sniffing", 4), ("07:44", "sitting", 6), ("07:50", "walking", 2),
    ("07:52", "lying_on_chest", 70),
    ("09:02:00", "whining", 5 / 60), ("09:02:20", "whining", 5 / 60), ("09:02:40", "whining", 6 / 60),
    ("09:03", "standing", 3), ("09:06", "walking", 5), ("09:11", "sniffing", 3),
    ("09:14", "lying_on_chest", 120),
    ("11:14", "standing", 1), ("11:15:05", "barking", 1 / 60), ("11:15:20", "barking", 1 / 60),
    ("11:15:40", "barking", 1 / 60), ("11:16", "sniffing", 3), ("11:19", "sitting", 5),
    ("11:24", "lying_on_chest", 95),
    ("12:59", "standing", 1), ("13:00", "galloping", 2), ("13:02", "trotting", 2),
    ("13:04", "walking", 4), ("13:08", "sniffing", 5), ("13:13", "sitting", 4),
    ("13:17", "lying_on_chest", 80),
    ("14:37", "standing", 2), ("14:39", "walking", 3), ("14:42", "sniffing", 4),
    ("14:46", "button_press", 0), ("14:47", "sitting", 8), ("14:55", "lying_on_chest", 65),
    ("16:00", "trotting", 3), ("16:03", "walking", 5), ("16:08", "sniffing", 4),
    ("16:12", "lying_on_chest", 55), ("17:07", "standing", 3), ("17:10", "walking", 4),
    ("17:14", "sitting", 10), ("17:24", "lying_on_chest", 90),
]

SOURCE = {"barking": "audio", "whining": "audio", "button_press": "sensor"}

# which clip/moment gives a fitting picture for each label
THUMB_FROM = {
    "lying_on_chest": ("sniffing-2", 0.05), "sniffing": ("sniffing-2", 0.3),
    "standing": ("barking-1", 0.3), "sitting": ("sniffing-1", 0.3),
    "walking": ("running-1", 0.3), "trotting": ("running-1", 0.55), "galloping": ("running-1", 0.8),
    "barking": ("barking-1", 0.55), "whining": ("barking-1", 0.8),
}


def make_thumbs() -> dict:
    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
    thumbs = {}
    for label, (clip, frac) in THUMB_FROM.items():
        path = ROOT / "data" / "demo_videos" / f"{clip}.mp4"
        if not path.exists():
            continue
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * frac))
        ok, frame = cap.read()
        if not ok:
            continue
        r = model.predict(frame, classes=[DOG_CLASS_ID], verbose=False)[0]
        if r.boxes is None or len(r.boxes) == 0:
            continue
        boxes = r.boxes.xyxy.cpu().numpy()
        i = int(((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])).argmax())
        thumbs[label] = _thumb_jpeg(frame, tuple(boxes[i]))
    return thumbs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="delete today's history first")
    args = ap.parse_args()

    day_file = history.HISTORY_DIR / f"{history.local_today()}.jsonl"
    if day_file.exists():
        if not args.replace:
            raise SystemExit(f"{day_file.name} already has history -- rerun with --replace to overwrite it")
        day_file.unlink()

    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=1)  # leave the last minute for live events
    thumbs = make_thumbs()
    events = []
    for n, (hhmm, label, minutes) in enumerate(DAY):
        parts = [int(x) for x in hhmm.split(":")] + [0]
        start = now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
        if start >= cutoff:
            break
        end = min(start + timedelta(minutes=minutes), cutoff)
        ev = {
            "schema_version": SCHEMA_VERSION, "event_id": f"evt_seed{n:03d}", "subject_id": "demo_dog_01",
            "source": SOURCE.get(label, "video"), "label": label, "confidence": 0.9,
            "started_at": to_iso(start.astimezone(timezone.utc)), "ended_at": to_iso(end.astimezone(timezone.utc)),
            "zone": "unknown", "evidence": {"detector": "demo_seed"},
        }
        validate(ev, "behavior_event")
        events.append(ev)

    alerts = []
    for i in range(len(events)):  # replay through the real rules, as if live
        alerts.extend(evaluate_rules(events[: i + 1], alerts))
    resolve_stale(alerts, now.astimezone(timezone.utc))

    for ev in events:
        history.append("event", ev)
        if ev["label"] in thumbs:
            history.save_thumb(ev["event_id"], thumbs[ev["label"]])
    for a in alerts:
        history.append("alert", a)
    print(f"Seeded {len(events)} events and {len(alerts)} alerts up to {cutoff:%H:%M} into {day_file.name} "
          f"({len(thumbs)} thumbnail types). Restart the server to load them.")


if __name__ == "__main__":
    main()
