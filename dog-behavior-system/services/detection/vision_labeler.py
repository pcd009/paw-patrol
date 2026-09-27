"""Labels the dog's behaviour from a short strip of cropped frames (oldest first).

Primary: Claude Sonnet vision, zero-shot, on ~3 crops spanning ~1 s -- enough for it to judge
gait (walk/trot/gallop) from leg positions, which bbox speed can't do when the camera moves.
video_detector.py calls this from a background worker so detection never blocks on it.
Fallback (no API key, refusal, or any API error): bbox aspect-ratio heuristic for posture.
See docs/DATASETS.md Part A row 2/4.
"""
from __future__ import annotations

import base64
from typing import List, Sequence, Tuple

from app.config import get as cfg_get
from services.llm_common import call_claude_json, clamp01

BREED = (cfg_get("dog", default={}) or {}).get("breed", "dog")

POSTURES = ["walking", "trotting", "galloping", "standing", "sitting", "lying_on_chest"]
# barking is left to the audio detector: it can't be judged reliably from a few silent frames
ACTIVITIES = ["none", "sniffing"]
LABELS = POSTURES + ["sniffing"]

SYSTEM_PROMPT = (
    f"You describe a pet dog (a {BREED}) for a home monitoring system. You get "
    "a few cropped video frames of the same dog in time order, roughly 0.5 s apart. The camera "
    "may pan or follow the dog, so judge motion from the body and legs relative to the ground, "
    "not from where the dog sits in the frame. Report two things.\n"
    "posture (exactly one):\n"
    "- walking: slow gait, legs move one at a time, usually 2-3 feet on the ground\n"
    "- trotting: brisker gait, diagonal leg pairs move together, back fairly level\n"
    "- galloping: running fast, body stretching and bunching, splashing/bounding\n"
    "- standing: upright on its legs and not travelling (include standing up on hind legs "
    "with front paws on a fence/ledge)\n"
    "- sitting: hindquarters on the ground, front legs straight\n"
    "- lying_on_chest: lying down on chest/belly, sphinx-like, head up or resting on paws\n"
    "activity (exactly one):\n"
    "- sniffing: nose pointed DOWN, held at the ground or touching/right next to an object, "
    "investigating it\n"
    "- none: not clearly sniffing. Common mistakes to avoid: a dog looking around, barking, or "
    "lifting its nose into the air is NOT sniffing\n"
    "Confidence is your probability (0 to 1) that the combined description is right. Lower it "
    "when the body is cut off or blurry. Respond with only the required JSON."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["posture", "activity", "confidence"],
    "properties": {
        "posture": {"enum": POSTURES},
        "activity": {"enum": ACTIVITIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

MAX_SIDE = 384  # px; keeps each image cheap in tokens


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


def _jpeg_b64(crop) -> str:
    import cv2
    h, w = crop.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode()


def label_strip(crops: Sequence, times: Sequence[float]) -> Tuple[str, float, str, str]:
    """crops: dog crops oldest->newest; times: their timestamps (s).
    Returns (label, confidence, model_label, description) where model_label is CLAUDE_MODEL
    or 'mock' and description is e.g. 'lying_on_chest + sniffing'."""
    crops = [c for c in crops if c is not None and getattr(c, "size", 0) > 0]
    if not crops:
        label, conf = heuristic_label(None)
        return label, conf, "mock", label
    try:
        content: List[dict] = []
        for i, (crop, t) in enumerate(zip(crops, times)):
            content.append({"type": "text", "text": f"Frame {i + 1} (t = {t - times[0]:+.2f} s)"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": _jpeg_b64(crop)}})
        content.append({"type": "text", "text": "Label the dog's current behaviour."})
    except Exception:
        label, conf = heuristic_label(crops[-1])
        return label, conf, "mock", label
    data, model_label = call_claude_json(SYSTEM_PROMPT, content, SCHEMA, max_tokens=300)
    if data is None:
        label, conf = heuristic_label(crops[-1])
        return label, conf, "mock", label
    posture, activity = data["posture"], data["activity"]
    # the activity is the more informative behaviour when present (e.g. lying + sniffing)
    label = activity if activity != "none" else posture
    desc = posture if activity == "none" else f"{posture} + {activity}"
    return label, clamp01(data["confidence"]), model_label, desc


def label_crop(crop) -> Tuple[str, float, str, str]:
    """Single-frame convenience wrapper (kept for compatibility)."""
    return label_strip([crop], [0.0])
