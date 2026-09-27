"""Live/webcam detector runner: video (+ optional audio) -> BehaviorEvents -> POST to the
orchestrator. Every event is validated against contracts/schemas/behavior_event.json before it
is sent. Also POSTs the latest annotated JPEG frame for the dashboard.

    python -m services.detection.run --video 0 --server http://localhost:8000
    python -m services.detection.run --video 0 --audio mic --server http://localhost:8000
    python -m services.detection.run --video data/demo_videos/clip1.mp4 --audio data/demo_videos/clip1.mp4 --server http://localhost:8000
"""
from __future__ import annotations

import argparse
import threading
import time
from datetime import datetime, timezone

import requests
from typing import Optional

from app.config import get as cfg_get
from app.io.inputs import AudioSource, VideoSource
from contracts.validate import validate
from services.detection.audio_detector import AudioDetector, merge_events as merge_audio_events
from services.detection.segmenter import Segmenter
from services.detection.video_detector import VideoDetector


class SharedZone:
    def __init__(self):
        self.zone = "unknown"


def post_event(server: str, event: dict) -> None:
    thumb = event.pop("_thumb", None)
    try:
        validate(event, "behavior_event")
    except ValueError as e:
        print(f"[run] SKIP invalid event: {e}")
        return
    try:
        requests.post(f"{server}/api/events", json=event, timeout=3)
        if thumb:
            requests.post(f"{server}/api/events/{event['event_id']}/thumb", data=thumb,
                          headers={"Content-Type": "image/jpeg"}, timeout=3)
        # the orchestrator's ConsoleSink logs the event -- no duplicate line here
    except Exception as e:
        print(f"[run] POST /api/events failed: {e}")


def run_display(vs: VideoSource, detector: VideoDetector, server: str, fps: float) -> None:
    """Post every new frame (with the latest detection box drawn on it) as it arrives --
    the source's native fps -- capped at `fps` if > 0. Independent of detection speed."""
    import cv2
    session = requests.Session()
    min_period = 1.0 / fps if fps and fps > 0 else 0.0
    last_seq = 0
    while not vs.ended:
        started = time.monotonic()
        item = vs.wait_newer(last_seq)
        if item is not None and item[2] != last_seq:
            _, frame, last_seq = item
            if detector.overlay:
                frame = VideoDetector._draw(frame, *detector.overlay)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                try:
                    session.post(f"{server}/api/frame", data=buf.tobytes(),
                                 headers={"Content-Type": "image/jpeg"}, timeout=1)
                except Exception as e:
                    print(f"[run] POST /api/frame failed: {e}")
                    time.sleep(1)
        if min_period:
            time.sleep(max(0.0, min_period - (time.monotonic() - started)))


def run_video(video_src: str, server: str, subject_id: str, shared_zone: SharedZone,
              loop: Optional[bool] = None) -> None:
    vs = VideoSource(video_src, target_fps=cfg_get("adapters", "video_fps_sample", default=8),
                     loop_files=cfg_get("adapters", "loop_video_files", default=True) if loop is None else loop).start()
    detector = VideoDetector(vs)
    threading.Thread(target=run_display, daemon=True,
                     args=(vs, detector, server, cfg_get("adapters", "display_fps", default=0))).start()
    t0_wall = datetime.now(timezone.utc)
    stable_s = cfg_get("thresholds", "segmenter", "stable_window_s", default=1.0)
    seg = Segmenter(subject_id, "video", t0_wall, stable_window_s=stable_s,
                    max_segment_s=cfg_get("thresholds", "segmenter", "max_segment_s", default=5.0))
    session = requests.Session()
    last_current_post = 0.0
    try:
        for sample in detector.samples():
            now = time.monotonic()
            if now - last_current_post >= 0.25:  # live "what is the dog doing now" for the dashboard
                last_current_post = now
                try:
                    session.post(f"{server}/api/current", json={"current": detector.current}, timeout=1)
                except Exception:
                    pass
            if sample is not None:
                if sample.get("zone") and sample["zone"] != "unknown":
                    shared_zone.zone = sample["zone"]
                ev = seg.push(sample)
                if ev:
                    post_event(server, ev)
    finally:
        ev = seg.flush()
        if ev:
            post_event(server, ev)


def run_audio(audio_src: str, server: str, subject_id: str, shared_zone: SharedZone) -> None:
    src = AudioSource(audio_src)
    det = AudioDetector(
        src,
        window_s=cfg_get("thresholds", "audio", "window_s", default=1.0),
        hop_s=cfg_get("thresholds", "audio", "hop_s", default=0.5),
        threshold=cfg_get("thresholds", "audio", "prob_threshold", default=0.15),
    )
    t0_wall = datetime.now(timezone.utc)
    for ev in merge_audio_events(det.windows(), subject_id, t0_wall, zone_getter=lambda: shared_zone.zone):
        post_event(server, ev)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=None, help="webcam index, file path, or rtsp/http URL")
    ap.add_argument("--audio", default=None, help='"mic", or a wav/mp4 file path')
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--subject-id", default=None)
    ap.add_argument("--loop", dest="loop", action="store_true", default=None,
                    help="restart a video file when it ends (default: config adapters.loop_video_files)")
    ap.add_argument("--no-loop", dest="loop", action="store_false",
                    help="play a video file once, e.g. a recorded session")
    args = ap.parse_args()

    subject_id = args.subject_id or cfg_get("subject_id", default="demo_dog_01")
    shared_zone = SharedZone()

    threads = []
    if args.video is not None:
        threads.append(threading.Thread(
            target=run_video, args=(args.video, args.server, subject_id, shared_zone, args.loop), daemon=True))
    if args.audio is None and args.video and not args.video.isdigit() and "://" not in args.video:
        from app.io.media import has_audio
        if has_audio(args.video):  # a clip with sound: listen to it too
            args.audio = args.video
            print(f"[run] {args.video} has an audio track -- running the bark/whine detector on it")
    if args.audio is not None:
        threads.append(threading.Thread(
            target=run_audio, args=(args.audio, args.server, subject_id, shared_zone), daemon=True))

    if not threads:
        print("[run] nothing to do -- pass --video and/or --audio")
        return

    for th in threads:
        th.start()
    try:
        for th in threads:
            th.join()
    except KeyboardInterrupt:
        print("\n[run] stopping")


if __name__ == "__main__":
    main()
