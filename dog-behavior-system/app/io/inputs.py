"""Swappable input adapters. Tomorrow's hardware plugs in here without touching detection code:

  - VideoSource:  webcam index today -> phone IP-cam / RTSP URL tomorrow (just change config.yaml).
  - AudioSource:  wav file today -> mic today/tomorrow; ESP32 doesn't do audio.
  - SensorSource: HTTP POST today (ESP32 button hits the orchestrator directly, no polling
                  needed) -> SerialSensorSource / ImuStreamSource if wired over USB-serial instead.
"""
from __future__ import annotations

import queue
import time
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np


class VideoSource:
    """Wraps cv2.VideoCapture. `source` is a webcam index ("0"), a file path, or an
    rtsp://... / http://... URL (e.g. a phone running an IP-cam app)."""

    def __init__(self, source: str, target_fps: float = 8.0):
        self.source_raw = source
        self.target_fps = target_fps
        self._cap = None

    def _resolve(self):
        s = self.source_raw
        if isinstance(s, str) and s.isdigit():
            return int(s)
        return s

    def open(self) -> "VideoSource":
        import cv2
        self._cap = cv2.VideoCapture(self._resolve())
        if not self._cap.isOpened():
            raise RuntimeError(f"VideoSource: could not open {self.source_raw!r}")
        return self

    def frames(self) -> Iterator[Tuple[float, "np.ndarray"]]:
        """Yield (t_seconds_since_start, frame_bgr) sampled at ~target_fps."""
        import cv2
        if self._cap is None:
            self.open()
        cap = self._cap
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        native_fps = native_fps if native_fps and native_fps > 0.1 else 30.0
        step = max(1, round(native_fps / self.target_fps))
        t0 = time.monotonic()
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                yield (time.monotonic() - t0), frame
            idx += 1

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()


class AudioSource:
    """Yields mono float32 PCM chunks at self.sample_rate. audio_detector.py buffers these
    into 1s windows with a 0.5s hop -- this class just supplies raw audio, live or from file."""

    def __init__(self, source: Optional[str], sample_rate: int = 16000, chunk_s: float = 0.1, realtime: bool = True):
        self.source = source  # None | "mic" | path to wav/mp4
        self.sample_rate = sample_rate
        self.chunk_s = chunk_s
        self.realtime = realtime

    def chunks(self) -> Iterator["np.ndarray"]:
        if self.source in (None, "none", ""):
            return
        if self.source == "mic":
            yield from self._mic_chunks()
        else:
            yield from self._file_chunks()

    def _mic_chunks(self) -> Iterator["np.ndarray"]:
        import sounddevice as sd
        block = int(self.sample_rate * self.chunk_s)
        q: "queue.Queue" = queue.Queue()

        def cb(indata, frames, time_info, status):
            q.put(indata[:, 0].copy())

        with sd.InputStream(samplerate=self.sample_rate, channels=1, blocksize=block, callback=cb):
            while True:
                yield q.get()

    def _file_chunks(self) -> Iterator["np.ndarray"]:
        import librosa
        y, _sr = librosa.load(self.source, sr=self.sample_rate, mono=True)
        block = int(self.sample_rate * self.chunk_s)
        for i in range(0, len(y), block):
            chunk = y[i:i + block]
            if len(chunk) < block:
                chunk = np.pad(chunk, (0, block - len(chunk)))
            yield chunk
            if self.realtime:
                time.sleep(self.chunk_s)


class SensorSource:
    """Base interface for anything that produces raw sensor events (button presses, IMU labels)
    outside the video/audio pipeline. `events()` yields dicts with at least a "label" key; the
    caller (services/detection/run.py or the orchestrator) fills in subject_id/zone/evidence and
    validates against behavior_event.json before sending."""

    def events(self) -> Iterator[dict]:
        raise NotImplementedError


class HttpPostSensorSource(SensorSource):
    """Placeholder: in this system the ESP32 button POSTs straight to the orchestrator's
    /api/sensor/button endpoint (no JSON library needed on the device -- see app/orchestrator).
    Nothing needs to poll for it, so this class is a documented no-op to keep the adapter
    interface symmetric with the serial/IMU variants below."""

    def events(self) -> Iterator[dict]:
        return iter(())


class SerialSensorSource(SensorSource):
    """Stub for a button wired over USB-serial instead of Wi-Fi. Expects newline-delimited
    tokens, e.g. the ESP32 sketch prints b"BUTTON\\n" on each press.
    Needs `pip install pyserial` (not in requirements.txt -- only if you actually wire this up)."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud

    def events(self) -> Iterator[dict]:
        import serial  # type: ignore
        with serial.Serial(self.port, self.baud, timeout=1) as ser:
            while True:
                line = ser.readline().decode(errors="ignore").strip()
                if line == "BUTTON":
                    yield {"label": "button_press", "source": "sensor"}


class ImuStreamSource(SensorSource):
    """Stub for an MPU6050 (or similar) collar streaming raw accel/gyro samples. Buffers into
    windows and classifies with models/imu_clf.joblib (trained on the Vehkaoja et al. dataset --
    see docs/RESULTS.md for the sensor/unit/rate assumptions a real MPU6050 must be resampled
    and rescaled to before this produces sane labels).

    `sample_source` is any iterable of (ax, ay, az, gx, gy, gz) tuples, e.g. lines read off a
    serial port and parsed as CSV floats.
    """

    def __init__(self, sample_source: Iterable[Sequence[float]], sample_rate_hz: int = 100, window_s: float = 2.0):
        self.sample_source = sample_source
        self.sample_rate_hz = sample_rate_hz
        self.window_s = window_s
        self._bundle = None

    def _load(self):
        if self._bundle is None:
            import joblib
            from pathlib import Path
            path = Path(__file__).resolve().parent.parent.parent / "models" / "imu_clf.joblib"
            self._bundle = joblib.load(path)
        return self._bundle

    def events(self) -> Iterator[dict]:
        from services.detection.imu_features import compute_features, windows

        bundle = self._load()
        window_n = int(bundle.get("sample_rate_hz", self.sample_rate_hz) * bundle.get("window_s", self.window_s))
        hop_n = max(1, window_n // 2)
        model = bundle["model"]
        for win in windows(self.sample_source, window_n, hop_n):
            feats = compute_features(win)
            label = str(model.predict([feats])[0])
            try:
                confidence = float(max(model.predict_proba([feats])[0]))
            except Exception:
                confidence = 0.6
            yield {"label": label, "source": "sensor", "confidence": confidence}
