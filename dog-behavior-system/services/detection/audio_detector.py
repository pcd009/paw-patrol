"""1s AST windows / 0.5s hop over an AudioSource -> bark/whine BehaviorEvents.

Maps AudioSet labels (docs/DATASETS.md Part C): Bark/Bow-wow/Yip/Dog -> barking;
Whimper (dog)/Whimper/Howl -> whining. Deliberately simple: top score per group vs. a
probability threshold, consecutive positive windows of the same label merge into one event.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable, Iterator, Optional, Tuple

import numpy as np

from app.config import get as cfg_get
from contracts.common import SCHEMA_VERSION, new_id, to_iso

BARK_LABELS = set(cfg_get("thresholds", "audio", "bark_ast_labels", default=["Bark", "Bow-wow", "Yip", "Dog"]))
WHINE_LABELS = set(cfg_get("thresholds", "audio", "whine_ast_labels", default=["Whimper (dog)", "Whimper", "Howl"]))

_pipeline = None


def _load_pipeline():
    global _pipeline
    if _pipeline is None:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline("audio-classification", model="MIT/ast-finetuned-audioset-10-10-0.4593")
    return _pipeline


def classify_window(samples: "np.ndarray", sample_rate: int, threshold: float) -> Optional[str]:
    """samples: 1D float32 array (~1s @ sample_rate). Returns 'barking' / 'whining' / None."""
    clf = _load_pipeline()
    preds = clf({"array": samples, "sampling_rate": sample_rate}, top_k=15)
    best_bark = max((p["score"] for p in preds if p["label"] in BARK_LABELS), default=0.0)
    best_whine = max((p["score"] for p in preds if p["label"] in WHINE_LABELS), default=0.0)
    if best_bark < threshold and best_whine < threshold:
        return None
    return "barking" if best_bark >= best_whine else "whining"


class AudioDetector:
    def __init__(self, audio_source, window_s: float = 1.0, hop_s: float = 0.5, threshold: float = 0.15):
        self.audio_source = audio_source
        self.window_s = window_s
        self.hop_s = hop_s
        self.threshold = threshold

    def windows(self) -> Iterator[Tuple[float, Optional[str]]]:
        """Yields (t_seconds_since_start, label_or_None) for each 1s/0.5s-hop window."""
        sr = self.audio_source.sample_rate
        window_n = int(sr * self.window_s)
        hop_n = int(sr * self.hop_s)
        buf = np.zeros(0, dtype=np.float32)
        total_consumed = 0
        for chunk in self.audio_source.chunks():
            buf = np.concatenate([buf, np.asarray(chunk, dtype=np.float32)])
            while len(buf) >= window_n:
                window = buf[:window_n]
                t = total_consumed / sr
                label = classify_window(window, sr, self.threshold)
                yield t, label
                buf = buf[hop_n:]
                total_consumed += hop_n


def merge_events(
    window_iter: Iterable[Tuple[float, Optional[str]]],
    subject_id: str,
    t0_wall: datetime,
    zone_getter: Optional[Callable[[], str]] = None,
) -> Iterator[dict]:
    """Consecutive same non-None labels merge into one BehaviorEvent. `zone_getter()` (if given)
    supplies the dog's last known video zone -- audio events have no visual zone of their own."""

    def make_event(label: str, start_t: float, end_t: float) -> dict:
        zone = (zone_getter() if zone_getter else None) or "unknown"
        started = t0_wall + timedelta(seconds=start_t)
        ended = t0_wall + timedelta(seconds=max(end_t, start_t))
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": new_id("evt"),
            "subject_id": subject_id,
            "source": "audio",
            "label": label,
            "confidence": 0.7,
            "started_at": to_iso(started),
            "ended_at": to_iso(ended),
            "zone": zone,
            "evidence": {"detector": "ast-audioset"},
        }

    cur_label: Optional[str] = None
    cur_start = 0.0
    cur_end = 0.0
    for t, label in window_iter:
        if label == cur_label:
            cur_end = t
            continue
        if cur_label is not None:
            yield make_event(cur_label, cur_start, cur_end)
        cur_label, cur_start, cur_end = label, t, t
    if cur_label is not None:
        yield make_event(cur_label, cur_start, cur_end)
