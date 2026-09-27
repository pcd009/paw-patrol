"""Refines a stationary/slow dog crop into sitting/standing/lying_on_chest/sniffing/walking.

Primary: Claude Sonnet vision, zero-shot (rate-limited by the caller in video_detector.py).
Fallback (no API key, refusal, or any API error): a bbox aspect-ratio heuristic -- rough but
keeps the demo running end-to-end without a key. See docs/DATASETS.md Part A row 2/4.
"""
from __future__ import annotations

import base64
from typing import Tuple

from services.llm_common import call_claude_json

LABELS = ["sitting", "standing", "lying_on_chest", "sniffing", "walking"]

SYSTEM_PROMPT = (
    "You are labelling a single cropped photo of a pet Labrador retriever for a home monitoring "
    "system. Choose exactly one posture label from: sitting, standing, lying_on_chest, sniffing, "
    "walking. sniffing means head lowered close to the ground/floor. lying_on_chest means lying "
    "down with body weight on the chest/sternum, not on its side or back. If unsure, still pick "
    "the closest label and report a lower confidence. Respond with only the required JSON."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label", "confidence"],
    "properties": {
        "label": {"enum": LABELS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def heuristic_label(crop) -> Tuple[str, float]:
    """Cheap, no-ML fallback and cheap "did the state change?" probe: bbox aspect ratio."""
    if crop is None or getattr(crop, "size", 0) == 0:
        return "standing", 0.3
    h, w = crop.shape[:2]
    ratio = w / max(1, h)
    if ratio > 1.6:
        return "lying_on_chest", 0.45
    if ratio > 1.15:
        return "sniffing", 0.4
    if ratio > 0.85:
        return "sitting", 0.4
    return "standing", 0.4


def label_crop(crop) -> Tuple[str, float, str]:
    """Returns (label, confidence, model_label) where model_label is CLAUDE_MODEL or 'mock'."""
    if crop is None or getattr(crop, "size", 0) == 0:
        label, conf = heuristic_label(crop)
        return label, conf, "mock"
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", crop)
    except Exception:
        ok = False
        buf = None
    if not ok:
        label, conf = heuristic_label(crop)
        return label, conf, "mock"
    b64 = base64.b64encode(buf.tobytes()).decode()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": "Label this dog's posture."},
    ]
    data, model_label = call_claude_json(SYSTEM_PROMPT, content, SCHEMA, max_tokens=200)
    if data is None:
        label, conf = heuristic_label(crop)
        return label, conf, "mock"
    return data["label"], float(data["confidence"]), model_label
