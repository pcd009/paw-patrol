"""Saves laptop webcam video + mic audio with laptop Unix timestamps -- the same clock the collar
receiver uses -- so a recording can later be synced with a collar session.

    data/recordings/<name>/
        video.mp4      frames as captured (mp4v)
        frames.csv     frame,t_laptop           Unix time each frame was captured
        audio.wav      mono PCM
        audio.json     {"t_start": <Unix time of the first sample>, "sample_rate": 16000}
        meta.json      name, start/end, camera, collar session (if known)
    data/recordings/current.txt   path of the recording being written
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
RECORDINGS_DIR = ROOT / "data" / "recordings"


def new_recording_dir(name: Optional[str] = None) -> Path:
    d = RECORDINGS_DIR / (name or datetime.now().strftime("%Y%m%d_%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    (RECORDINGS_DIR / "current.txt").write_text(str(d))
    meta = {"name": d.name, "started_at": time.time(), "started": datetime.now().isoformat(timespec="seconds")}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return d


def update_meta(d: Path, **fields) -> None:
    path = Path(d) / "meta.json"
    meta = json.loads(path.read_text()) if path.exists() else {}
    meta.update(fields)
    path.write_text(json.dumps(meta, indent=2))


class VideoRecorder:
    def __init__(self, d: Path, fps: float = 30.0):
        self.dir, self.fps = Path(d), fps
        self._writer = None
        self._csv = open(self.dir / "frames.csv", "w")
        self._csv.write("frame,t_laptop\n")
        self.frames = 0
        self._lock = threading.Lock()

    def write(self, frame, t: float) -> None:
        import cv2
        with self._lock:
            if self._writer is None:
                h, w = frame.shape[:2]
                self._writer = cv2.VideoWriter(str(self.dir / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                               self.fps, (w, h))
            self._writer.write(frame)
            self._csv.write(f"{self.frames},{t:.3f}\n")
            self.frames += 1
            if self.frames % 30 == 0:
                self._csv.flush()

    def close(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.release()
            self._csv.close()


class AudioRecorder:
    def __init__(self, d: Path, sample_rate: int = 16000):
        import soundfile as sf
        self.dir, self.sample_rate = Path(d), sample_rate
        self._f = sf.SoundFile(str(self.dir / "audio.wav"), "w", samplerate=sample_rate, channels=1)
        self.samples = 0
        self._lock = threading.Lock()

    def write(self, chunk, t_end: float) -> None:
        """chunk: mono float32 samples; t_end: Unix time the chunk's last sample was captured."""
        with self._lock:
            if self.samples == 0:
                t_start = t_end - len(chunk) / self.sample_rate
                (self.dir / "audio.json").write_text(json.dumps({"t_start": round(t_start, 3),
                                                                 "sample_rate": self.sample_rate}))
            self._f.write(chunk)
            self.samples += len(chunk)

    def close(self) -> None:
        with self._lock:
            self._f.close()
