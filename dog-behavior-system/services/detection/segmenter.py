"""Per-sample labels -> BehaviorEvents. A label must be stable for ~1s (stable_window_s) before
the segmenter commits the switch; brief flicker keeps the previous label (build plan step 3).
Works for any source: video (gait/posture) samples and merged audio (bark/whine) samples both
feed the same Segmenter shape: {"t": seconds_since_stream_start, "label", "confidence", "zone",
"detector"}.
"""
from __future__ import annotations

from collections import Counter, deque

from datetime import datetime, timedelta
from typing import Optional

from contracts.common import SCHEMA_VERSION, new_id, to_iso


class Segmenter:
    def __init__(self, subject_id: str, source: str, t0_wall: datetime, stable_window_s: float = 1.0,
                 max_segment_s: float = 5.0):
        self.subject_id = subject_id
        self.source = source
        self.t0_wall = t0_wall
        self.stable_window_s = stable_window_s
        self.max_segment_s = max_segment_s
        self._window = deque()
        self._label: Optional[str] = None
        self._zone: Optional[str] = None
        self._detector: Optional[str] = None
        self._start_t: Optional[float] = None
        self._end_t: Optional[float] = None
        self._confs = []
        self._thumb = None
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
            # not part of the contract: run.py pops this and uploads it separately
            "_thumb": self._thumb,
        }

    def _start(self, sample: dict) -> None:
        self._label = sample["label"]
        self._zone = sample.get("zone") or "unknown"
        self._detector = sample.get("detector")
        self._start_t = sample["t"]
        self._end_t = sample["t"]
        self._confs = [sample.get("confidence", 0.5)]
        self._thumb = sample.get("thumb")

    def push(self, sample: dict) -> Optional[dict]:
        """Feed one raw sample. Returns a BehaviorEvent dict whenever a segment closes, else None.
        A segment closes when (a) a different label holds the majority (>= 60%) of the last
        stable_window_s of samples -- so flicker between two new labels still counts as a change --
        or (b) it has run for max_segment_s, so long behaviours still reach the timeline live.
        Call flush() at the end of the stream to emit the last open segment."""
        label = sample["label"]
        t = sample["t"]
        self._window.append((t, label, sample))
        while self._window and t - self._window[0][0] > self.stable_window_s:
            self._window.popleft()

        if self._label is None:
            self._start(sample)
            return None

        counts = Counter(l for _, l, _ in self._window)
        top, n = counts.most_common(1)[0]
        window_full = t - self._window[0][0] >= 0.8 * self.stable_window_s

        if top != self._label and window_full and n / len(self._window) >= 0.6:
            first = next(x for x in self._window if x[1] == top)
            self._end_t = first[0]
            finished = self._emit()
            self._start({**first[2], "t": first[0]})
            self._end_t = t
            return finished

        self._end_t = t
        if label == self._label:
            self._confs.append(sample.get("confidence", 0.5))
        if t - self._start_t >= self.max_segment_s:
            finished = self._emit()
            self._start({**sample, "label": self._label, "zone": self._zone, "t": t})
            return finished
        return None

    def flush(self) -> Optional[dict]:
        ev = self._emit()
        self._label = None
        return ev
