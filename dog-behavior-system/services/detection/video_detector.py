"""Video -> per-frame behaviour samples.

YOLO (COCO-pretrained, class 16 = dog) finds and tracks the dog every sampled frame.
Label source, in priority order:
  1. Claude vision (vision_labeler.label_strip) on a strip of ~3 recent dog crops spanning
     ~1 s -- judges posture AND gait from the dog's body, so it works with a moving camera.
     Runs on a background worker every `interval_s`; detection never waits for it.
  2. Fallback (no key / stale result): camera-motion-compensated, smoothed bbox speed for gait,
     and bbox aspect ratio for posture.
Zone = whichever config.yaml zone rect contains the bbox centre. segmenter.py (owned by run.py)
turns this per-frame stream into BehaviorEvents.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

import numpy as np

from app.config import get as cfg_get
from services.detection.vision_labeler import heuristic_label, label_strip
from services.llm_common import is_live

ROOT = Path(__file__).resolve().parent.parent.parent
DOG_CLASS_ID = 16
FLOW_WIDTH = 160  # px width used for global (camera) motion estimation


def _zone_for_point(x: float, y: float, zones: Dict[str, list]) -> str:
    for name, rect in zones.items():
        x0, y0, x1, y1 = rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return "unknown"


def _padded_crop(frame, box, pad: float = 0.15):
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = box
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    return frame[max(0, int(y0 - py)):min(h, int(y1 + py)), max(0, int(x0 - px)):min(w, int(x1 + px))].copy()


def _thumb_jpeg(frame, box, max_side: int = 320) -> bytes:
    """Small JPEG of the dog (with some context) for the dashboard's activity list."""
    import cv2
    crop = _padded_crop(frame, box, pad=0.35)
    h, w = crop.shape[:2]
    scale = max_side / max(h, w, 1)
    if scale < 1:
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes() if ok else b""


class VideoDetector:
    def __init__(self, video_source):
        self.video_source = video_source
        self._model = None
        self._last_center: Optional[Tuple[float, float]] = None
        self._last_t: Optional[float] = None
        self._prev_gray = None
        self._last_h = 1.0
        self._speed_ema = 0.0
        self._crops = deque(maxlen=40)  # (t, crop) of recent dog sightings
        self._vision: Optional[tuple] = None  # (label, conf, model, desc, wall time)
        self._history: list = []  # recent Claude answers: (label, conf, desc, wall time)
        self._vote_n = int(cfg_get("thresholds", "vision_labeler", "vote_over", default=3))
        self._max_age_s = float(cfg_get("thresholds", "vision_labeler", "max_age_s", default=6.0))
        self._vision_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self.overlay: Optional[tuple] = None  # (box, label, zone) of the latest detection
        # what the dog is doing right now, for the dashboard (None = no dog in view)
        self.current: Optional[dict] = None

    def _load_model(self):
        if self._model is None:
            from ultralytics import YOLO
            weights = ROOT / "models" / "yolo11n.pt"
            self._model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
        return self._model

    # ---- Claude vision worker ------------------------------------------------------------
    def _vision_loop(self, interval_s: float, span_s: float, n_frames: int) -> None:
        while not self.video_source.ended:
            started = time.monotonic()
            crops = list(self._crops)
            if crops and crops[-1][0] >= (self._last_t or 0) - 0.5:  # dog seen just now
                t_end = crops[-1][0]
                picks = []
                for k in range(n_frames):  # evenly spaced targets over the last span_s
                    target = t_end - span_s * (n_frames - 1 - k) / max(1, n_frames - 1)
                    picks.append(min(crops, key=lambda c: abs(c[0] - target)))
                picks = list({id(p): p for p in picks}.values())  # dedupe, keep order
                label, conf, model, desc = label_strip([c for _, c in picks], [t for t, _ in picks])
                if model != "mock":
                    now = time.monotonic()
                    self._history = [h for h in self._history if now - h[3] <= self._max_age_s]
                    self._history = (self._history + [(label, conf, desc, now)])[-self._vote_n:]
                    # confidence-weighted vote over the last few answers: one odd answer
                    # (e.g. panting read as barking) no longer flips the label
                    score: Dict[str, float] = {}
                    for l, c, _, _ in self._history:
                        score[l] = score.get(l, 0.0) + c
                    winner = max(score, key=score.get)
                    mine = [h for h in self._history if h[0] == winner]
                    with self._vision_lock:
                        self._vision = (winner, sum(h[1] for h in mine) / len(mine), model,
                                        mine[-1][2], now)
            time.sleep(max(0.2, interval_s - (time.monotonic() - started)))

    def _fresh_vision(self, max_age_s: float):
        with self._vision_lock:
            v = self._vision
        if v and time.monotonic() - v[4] <= max_age_s:
            return v
        return None

    # ---- camera motion ------------------------------------------------------------------
    def _camera_shift(self, frame, box) -> Tuple[float, float]:
        """Apparent background motion (px, full-res) since the previous sampled frame."""
        import cv2
        h, w = frame.shape[:2]
        s = FLOW_WIDTH / w
        gray = cv2.cvtColor(cv2.resize(frame, (FLOW_WIDTH, int(h * s))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if box is not None:  # blank out the dog so it doesn't dominate the estimate
            x0, y0, x1, y1 = (int(v * s) for v in box[:4])
            gray[max(0, y0):y1, max(0, x0):x1] = gray.mean()
        prev, self._prev_gray = self._prev_gray, gray
        if prev is None or prev.shape != gray.shape:
            return 0.0, 0.0
        (dx, dy), _resp = cv2.phaseCorrelate(prev, gray)
        return dx / s, dy / s

    def samples(self) -> Iterator[Optional[dict]]:
        model = self._load_model()
        zones = cfg_get("zones", default={}) if cfg_get("zones_enabled", default=False) else {}
        gait_thr = cfg_get("thresholds", "gait", default={})
        stationary_max = gait_thr.get("stationary_max", 0.6)
        walking_max = gait_thr.get("walking_max", 2.2)
        trotting_max = gait_thr.get("trotting_max", 4.5)
        vis_cfg = cfg_get("thresholds", "vision_labeler", default={})
        interval_s = vis_cfg.get("interval_s", 2.0)
        max_age_s = vis_cfg.get("max_age_s", 6.0)
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._vision_loop, daemon=True,
                args=(interval_s, vis_cfg.get("strip_span_s", 1.0), vis_cfg.get("strip_frames", 3)))
            self._worker.start()

        claude_live = is_live()
        for t, frame in self.video_source.frames():
            h, w = frame.shape[:2]
            box = self._largest_dog_box(model, frame)
            cam_dx, cam_dy = self._camera_shift(frame, box)

            if box is None:
                self._last_center = None
                self.overlay = None
                self.current = None
                yield None  # no dog: nothing to segment this sample
                continue

            x0, y0, x1, y1, _det_conf = box
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            bbox_h = max(1.0, y1 - y0)
            speed = 0.0
            if self._last_center is not None and self._last_t is not None:
                dt = max(1e-3, t - self._last_t)
                # dog motion relative to the scene = bbox motion minus camera-induced motion
                dx = (cx - self._last_center[0]) - cam_dx
                dy = (cy - self._last_center[1]) - cam_dy
                lateral = ((dx ** 2 + dy ** 2) ** 0.5 / bbox_h) / dt
                # running toward/away from the camera shows up as the box growing/shrinking
                depth = abs(np.log(bbox_h / max(1.0, self._last_h))) / dt * 3.0
                speed = lateral + depth
            self._last_h = bbox_h
            self._speed_ema = 0.8 * self._speed_ema + 0.2 * min(speed, 3 * trotting_max)
            self._last_center = (cx, cy)
            self._last_t = t
            crop = _padded_crop(frame, (x0, y0, x1, y1))
            self._crops.append((t, crop))

            vision = self._fresh_vision(max_age_s)
            if vision is None and claude_live and self._vision is None:
                # Claude is available but hasn't answered yet: show the box, emit nothing
                self.overlay = ((x0, y0, x1, y1), "looking...", "unknown")
                self.current = {"label": "detecting", "description": "detecting", "confidence": 0.0,
                                "zone": "unknown", "source": "claude"}
                yield None
                continue
            if vision:
                label, confidence, model_used, desc, _ = vision
                detector = f"vision_strip:{model_used}"
            elif self._speed_ema >= trotting_max:
                label, confidence, detector = "galloping", 0.5, "gait_heuristic"
            elif self._speed_ema >= walking_max:
                label, confidence, detector = "trotting", 0.5, "gait_heuristic"
            elif self._speed_ema >= stationary_max:
                label, confidence, detector = "walking", 0.5, "gait_heuristic"
            else:
                label, confidence = heuristic_label(crop)
                detector = "aspect_heuristic"

            zone = _zone_for_point(cx / w, cy / h, zones)
            # run.py's display thread draws this over every live frame
            self.overlay = ((x0, y0, x1, y1), label, zone)
            self.current = {"label": label, "description": desc if vision else label,
                            "confidence": round(confidence, 2), "zone": zone,
                            "source": "claude" if vision else "estimate"}

            yield {
                "t": t, "label": label, "confidence": confidence, "zone": zone,
                "source": "video", "detector": f"yolo11n+{detector}",
                "thumb": _thumb_jpeg(frame, (x0, y0, x1, y1)),
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
