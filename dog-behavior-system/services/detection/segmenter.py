"""Per-sample labels -> BehaviorEvents. A label must be stable for ~1s (stable_window_s) before
the segmenter commits the switch; brief flicker keeps the previous label (build plan step 3).
Works for any source: video (gait/posture) samples and merged audio (bark/whine) samples both
feed the same Segmenter shape: {"t": seconds_since_stream_start, "label", "confidence", "zone",
"detector"}.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from contracts.common import SCHEMA_VERSION, new_id, to_iso


class Segmenter:
    def __init__(self, subject_id: str, source: str, t0_wall: datetime, stable_window_s: float = 1.0):
        self.subject_id = subject_id
        self.source = source
        self.t0_wall = t0_wall
        self.stable_window_s = stable_window_s
        self._label: Optional[str] = None
        self._zone: Optional[str] = None
        self._detector: Optional[str] = None
        self._start_t: Optional[float] = None
        self._end_t: Optional[float] = None
        self._confs = []
        self._pending_label: Optional[str] = None
        self._pending_start_t: Optional[float] = None

    def _wall(self, t: float) -> datetime:
        return self.t0_wall + timedelta(seconds=t)

    def _emit(self) -> Optional[dict]:
        if self._label is None:
            return None
        started = self._wall(self._start_t)
        ended = self._wall(max(self._end_t, self._start_t))
        conf = sum(self._confs) / max(1, len(self._confs))
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": new_id("evt"),
            "subject_id": self.subject_id,
            "source": self.source,
            "label": self._label,
            "confidence": round(min(1.0, max(0.0, conf)), 3),
            "started_at": to_iso(started),
            "ended_at": to_iso(ended),
            "zone": self._zone or "unknown",
            "evidence": {"detector": self._detector or "unknown"},
        }

    def _start(self, sample: dict) -> None:
        self._label = sample["label"]
        self._zone = sample.get("zone") or "unknown"
        self._detector = sample.get("detector")
        self._start_t = sample["t"]
        self._end_t = sample["t"]
        self._confs = [sample.get("confidence", 0.5)]

    def push(self, sample: dict) -> Optional[dict]:
        """Feed one raw sample. Returns a finished BehaviorEvent dict whenever a segment closes
        (label change confirmed stable for >= stable_window_s), else None. Call flush() at the
        end of the stream to emit the last open segment."""
        label = sample["label"]
        t = sample["t"]

        if self._label is None:
            self._start(sample)
            return None

        if label == self._label:
            self._pending_label = None
            self._end_t = t
            self._confs.append(sample.get("confidence", 0.5))
            return None

        if self._pending_label != label:
            # first sighting of a candidate new label -- don't commit yet
            self._pending_label = label
            self._pending_start_t = t
            self._end_t = t
            return None

        if t - self._pending_start_t < self.stable_window_s:
            # still flickering -- keep extending the current (previous) segment
            self._end_t = t
            return None

        # stable for >= stable_window_s: commit the switch at the moment the new label began
        self._end_t = self._pending_start_t
        finished = self._emit()
        self._start({**sample, "t": self._pending_start_t})
        self._end_t = t
        self._confs.append(sample.get("confidence", 0.5))
        self._pending_label = None
        return finished

    def flush(self) -> Optional[dict]:
        ev = self._emit()
        self._label = None
        return ev
