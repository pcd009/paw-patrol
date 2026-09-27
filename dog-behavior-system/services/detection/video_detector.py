"""Video -> per-frame behaviour samples.

YOLO (COCO-pretrained, class 16 = dog) finds and tracks the dog; a bbox-centroid speed
heuristic (bbox-heights moved per second) gives gait (stationary/walking/trotting/galloping).
When the dog is stationary/slow, vision_labeler.py refines the label to
sitting/standing/lying_on_chest/sniffing/walking (Claude vision, rate-limited; bbox
aspect-ratio heuristic fallback). Zone = whichever config.yaml zone rect contains the bbox
centre. segmenter.py (owned by run.py) turns this per-frame stream into BehaviorEvents.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from app.config import get as cfg_get
from services.detection.vision_labeler import heuristic_label, label_crop

ROOT = Path(__file__).resolve().parent.parent.parent
DOG_CLASS_ID = 16


def _zone_for_point(x: float, y: float, zones: Dict[str, list]) -> str:
    for name, rect in zones.items():
        x0, y0, x1, y1 = rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return "unknown"


class VideoDetector:
    def __init__(self, video_source):
        self.video_source = video_source
        self._model = None
        self._last_center: Optional[Tuple[float, float]] = None
        self._last_t: Optional[float] = None
        self._last_labeler_call = 0.0
        self._last_labeler_label: Optional[str] = None
        self._last_cheap_label: Optional[str] = None
        self.last_annotated_jpeg: Optional[bytes] = None

    def _load_model(self):
        if self._model is None:
            from ultralytics import YOLO
            weights = ROOT / "models" / "yolo11n.pt"
            self._model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
        return self._model

    def samples(self) -> Iterator[dict]:
        import cv2

        model = self._load_model()
        zones = cfg_get("zones", default={})
        gait_thr = cfg_get("thresholds", "gait", default={})
        stationary_max = gait_thr.get("stationary_max", 0.6)
        walking_max = gait_thr.get("walking_max", 2.2)
        trotting_max = gait_thr.get("trotting_max", 4.5)
        vis_cfg = cfg_get("thresholds", "vision_labeler", default={})
        min_interval = vis_cfg.get("min_interval_s", 3)
        max_interval = vis_cfg.get("max_interval_s", 10)

        for t, frame in self.video_source.frames():
            h, w = frame.shape[:2]
            box = self._largest_dog_box(model, frame)

            if box is None:
                self._last_center = None
                self.last_annotated_jpeg = self._encode(frame)
                continue

            x0, y0, x1, y1, _det_conf = box
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            bbox_h = max(1.0, y1 - y0)
            speed = 0.0
            if self._last_center is not None and self._last_t is not None:
                dt = max(1e-3, t - self._last_t)
                dx, dy = cx - self._last_center[0], cy - self._last_center[1]
                dist = (dx ** 2 + dy ** 2) ** 0.5
                speed = (dist / bbox_h) / dt
            self._last_center = (cx, cy)
            self._last_t = t

            if speed >= trotting_max:
                label, confidence, detector = "galloping", 0.7, "gait_heuristic"
            elif speed >= walking_max:
                label, confidence, detector = "trotting", 0.7, "gait_heuristic"
            elif speed >= stationary_max:
                label, confidence, detector = "walking", 0.7, "gait_heuristic"
            else:
                crop = frame[max(0, int(y0)):max(1, int(y1)), max(0, int(x0)):max(1, int(x1))]
                cheap_label, _cheap_conf = heuristic_label(crop)
                now = time.time()
                elapsed = now - self._last_labeler_call
                state_changed = cheap_label != self._last_cheap_label
                self._last_cheap_label = cheap_label
                if elapsed >= max_interval or (elapsed >= min_interval and state_changed):
                    label, confidence, model_used = label_crop(crop)
                    self._last_labeler_call = now
                    self._last_labeler_label = label
                    detector = f"vision_labeler:{model_used}"
                else:
                    label = self._last_labeler_label or cheap_label
                    confidence = 0.5
                    detector = "vision_labeler:cached"

            zone = _zone_for_point(cx / w, cy / h, zones)
            self.last_annotated_jpeg = self._encode(self._draw(frame, (x0, y0, x1, y1), label, zone))

            yield {
                "t": t, "label": label, "confidence": confidence, "zone": zone,
                "source": "video", "detector": f"yolo11n+{detector}",
            }

    @staticmethod
    def _largest_dog_box(model, frame):
        results = model.track(frame, persist=True, classes=[DOG_CLASS_ID], verbose=False)
        if not results:
            return None
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return None
        boxes = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        i = int(areas.argmax())
        x0, y0, x1, y1 = boxes[i]
        return float(x0), float(y0), float(x1), float(y1), float(confs[i])

    @staticmethod
    def _draw(frame, box, label, zone):
        import cv2
        x0, y0, x1, y1 = (int(v) for v in box)
        out = frame.copy()
        cv2.rectangle(out, (x0, y0), (x1, y1), (0, 200, 0), 2)
        cv2.putText(out, f"{label} ({zone})", (x0, max(0, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
        return out

    @staticmethod
    def _encode(frame) -> bytes:
        import cv2
        ok, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes() if ok else b""
