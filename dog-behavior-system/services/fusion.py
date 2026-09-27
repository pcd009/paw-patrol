"""Combines the collar with the laptop camera + mic. Used identically live and offline.

- Movement: the gyro/accelerometer decides the gait (walk / trot / gallop) whenever the collar says
  the dog is travelling -- it doesn't care about camera angle or panning. When the collar says the
  dog is still, a video gait label is corrected to "standing". Posture and sniffing stay Claude's.
- Sound: the laptop mic says WHAT the sound is (bark / whine); the collar mic, right at the dog,
  says WHOSE it is: loud at the collar -> "our dog"; quiet at the collar -> "probably not our dog"
  (a bark from a phone, TV or next door). Those don't count towards bark alerts.
- Collar-only movement: while the dog is out of the camera's view, the collar still reports gait.
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.config import get as cfg_get

GAITS = {"walking", "trotting", "galloping"}


class Fusion:
    def __init__(self, collar=None):
        self.collar = collar  # app.io.collar.CollarTimeline or None
        self.our_dog_mic = cfg_get("collar", "our_dog_mic_min", default=15)
        self.not_our_dog_mic = cfg_get("collar", "not_our_dog_mic_max", default=8)

    def live(self, t: float) -> bool:
        return self.collar is not None and self.collar.connected(t)

    def video_label(self, label: str, t: float) -> Tuple[str, Optional[str]]:
        """(fused label, note) for a video label at Unix time t. note -> goes into the detector tag."""
        if not self.live(t):
            return label, None
        act = self.collar.activity_at(t)
        if act in GAITS:
            return act, None if act == label else "collar_gait"
        if act == "still" and label in GAITS:
            return "standing", "collar_still"
        return label, None

    def sound_owner(self, t0: float, t1: float) -> Optional[str]:
        """'our_dog' | 'not_our_dog' | None (collar unavailable or unclear)."""
        if not self.live(t0):
            return None
        mic = self.collar.mic_max_between(t0 - 0.3, t1 + 0.3)
        if mic is None:
            return None
        if mic >= self.our_dog_mic:
            return "our_dog"
        if mic <= self.not_our_dog_mic:
            return "not_our_dog"
        return None

    def tag_sound_event(self, ev: dict, t0: float, t1: float) -> dict:
        owner = self.sound_owner(t0, t1)
        if owner:
            ev["evidence"]["detector"] = ev["evidence"].get("detector", "") + f"+collar:{owner}"
            ev["confidence"] = round(min(0.95, ev["confidence"] + 0.2), 3) if owner == "our_dog" \
                else round(max(0.2, ev["confidence"] - 0.35), 3)
        return ev


def is_not_our_dog(event: dict) -> bool:
    return "collar:not_our_dog" in event.get("evidence", {}).get("detector", "")
