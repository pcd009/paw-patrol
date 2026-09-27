"""Demo mode: a pre-filled ~3-hour sample story ending at server start; live events then append.

Every story event is tagged evidence.detector = "demo_seed" (the dashboard shows a demo badge).
Alerts come from the real rule engine. Thumbnails are dog crops from data/demo_videos/, cut once
and cached in data/demo_thumbs/.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from contracts.common import SCHEMA_VERSION, to_iso
from services.context_rules.engine import evaluate_rules, resolve_stale

ROOT = Path(__file__).resolve().parent.parent.parent
THUMB_CACHE = ROOT / "data" / "demo_thumbs"

# (minutes before start, label, duration in minutes): owner left ~3 h ago
STORY = [
    (185, "whining", 5 / 60), (184.7, "whining", 5 / 60), (184.4, "whining", 6 / 60),
    (184, "standing", 3), (181, "walking", 4), (177, "sniffing", 3), (174, "lying_on_chest", 52),
    (122, "standing", 1), (121, "barking", 1 / 60), (120.7, "barking", 1 / 60), (120.4, "barking", 1 / 60),
    (120, "sniffing", 3), (117, "sitting", 5), (112, "lying_on_chest", 45),
    (67, "standing", 1), (66, "galloping", 2), (64, "trotting", 2), (62, "walking", 4),
    (58, "sniffing", 5), (53, "button_press", 0), (52.5, "sitting", 6), (46, "lying_on_chest", 38),
    (8, "standing", 2), (6, "walking", 3), (3, "sniffing", 2.5),
]
SOURCE = {"barking": "audio", "whining": "audio", "button_press": "sensor"}
THUMB_FROM = {  # label -> (clip, position in clip)
    "lying_on_chest": ("sniffing-2", 0.05), "sniffing": ("sniffing-2", 0.3),
    "standing": ("barking-1", 0.3), "sitting": ("sniffing-1", 0.3),
    "walking": ("running-1", 0.3), "trotting": ("running-1", 0.55), "galloping": ("running-1", 0.8),
}


def demo_thumbs() -> Dict[str, bytes]:
    """label -> jpeg, cut from the demo clips once (YOLO dog crop), then cached on disk."""
    out: Dict[str, bytes] = {}
    missing = []
    for label in THUMB_FROM:
        f = THUMB_CACHE / f"{label}.jpg"
        if f.exists():
            out[label] = f.read_bytes()
        else:
            missing.append(label)
    if not missing:
        return out
    try:
        import cv2
        from ultralytics import YOLO
        from services.detection.video_detector import DOG_CLASS_ID, _thumb_jpeg
        weights = ROOT / "models" / "yolo11n.pt"
        model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
        THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        for label in missing:
            clip, frac = THUMB_FROM[label]
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
            out[label] = _thumb_jpeg(frame, tuple(boxes[i]))
            (THUMB_CACHE / f"{label}.jpg").write_bytes(out[label])
    except Exception as e:  # no clips / no YOLO: the story still works, just without photos
        print(f"[demo] thumbnails unavailable: {e}")
    return out


def build_demo_story(start: datetime) -> Tuple[List[dict], List[dict], Dict[str, bytes]]:
    """(events, alerts, {event_id: jpeg}) for the story ending just before `start`."""
    start = start.astimezone(timezone.utc)
    thumbs_by_label = demo_thumbs()
    events, thumbs = [], {}
    for n, (mins_before, label, minutes) in enumerate(STORY):
        s = start - timedelta(minutes=mins_before)
        e = s + timedelta(minutes=minutes)
        ev = {
            "schema_version": SCHEMA_VERSION, "event_id": f"evt_demo{n:03d}", "subject_id": "demo_dog_01",
            "source": SOURCE.get(label, "video"), "label": label, "confidence": 0.9,
            "started_at": to_iso(s), "ended_at": to_iso(min(e, start)), "zone": "unknown",
            "evidence": {"detector": "demo_seed"},
        }
        events.append(ev)
        if label in thumbs_by_label:
            thumbs[ev["event_id"]] = thumbs_by_label[label]
    alerts: List[dict] = []
    for i in range(len(events)):  # the real rule engine, replayed in order
        alerts.extend(evaluate_rules(events[: i + 1], alerts))
    resolve_stale(alerts, start)
    return events, alerts, thumbs
