"""Detector runner: laptop camera/mic (+ the collar's session files) -> BehaviorEvents -> orchestrator.

    python -m services.detection.run --video 0 --audio mic                       # live
    python -m services.detection.run --video 0 --audio mic --collar ../paw-patrol/sessions --record
    python -m services.detection.run --video data/demo_videos/clip.mp4 --no-loop  # a file as "live"
    python -m services.detection.run --playback data/recordings/<name>          # processed recording

--collar     a collar session folder, or the collar's sessions dir (follows its current.txt). Read
             from the files the collar receiver writes; the gyro drives gait, the collar mic says
             whether a bark was our dog's (services/fusion.py).
--record     also save the camera video + mic audio with Unix timestamps (app/io/recording.py), for
             syncing with the collar session later (scripts/process_session.py).
--playback   play a recording's video and post its processed events in sync (for the demo).
Every event is validated against contracts/schemas/behavior_event.json before it is sent.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from app.config import get as cfg_get
from app.io.inputs import AudioSource, VideoSource
from contracts.common import to_iso
from contracts.validate import validate
from services.detection.audio_detector import AudioDetector, merge_events as merge_audio_events
from services.detection.segmenter import Segmenter
from services.detection.video_detector import VideoDetector
from services.fusion import GAITS, Fusion


class Shared:
    """State shared between the video, audio and collar threads."""

    def __init__(self, fusion: Fusion):
        self.zone = "unknown"
        self.fusion = fusion
        self.dog_last_seen = 0.0  # Unix time the camera last saw the dog


def _unix(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


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


def run_display(vs: VideoSource, overlay_src, server: str, fps: float) -> None:
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
            if getattr(overlay_src, "overlay", None):
                frame = VideoDetector._draw(frame, *overlay_src.overlay)
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


def run_video(video_src: str, server: str, subject_id: str, shared: Shared,
              loop: Optional[bool] = None, recorder=None) -> None:
    vs = VideoSource(video_src, target_fps=cfg_get("adapters", "video_fps_sample", default=8),
                     loop_files=cfg_get("adapters", "loop_video_files", default=True) if loop is None else loop,
                     recorder=recorder).start()
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
            if sample is not None:
                now_unix = time.time()
                shared.dog_last_seen = now_unix
                label, note = shared.fusion.video_label(sample["label"], now_unix)
                if note:  # the collar corrected the gait (or said the dog is actually still)
                    sample["label"] = label
                    sample["detector"] += f"+{note}"
                    if detector.current:
                        detector.current.update(label=label, description=label)
                    if detector.overlay:
                        detector.overlay = (detector.overlay[0], label, detector.overlay[2])
                if sample.get("zone") and sample["zone"] != "unknown":
                    shared.zone = sample["zone"]
            now = time.monotonic()
            if now - last_current_post >= 0.25:  # live "what is the dog doing now" for the dashboard
                last_current_post = now
                try:
                    session.post(f"{server}/api/current", json={"current": detector.current}, timeout=1)
                except Exception:
                    pass
            if sample is not None:
                ev = seg.push(sample)
                if ev:
                    post_event(server, ev)
    finally:
        ev = seg.flush()
        if ev:
            post_event(server, ev)


def run_audio(audio_src: str, server: str, subject_id: str, shared: Shared, recorder=None) -> None:
    src = AudioSource(audio_src, recorder=recorder)
    det = AudioDetector(
        src,
        window_s=cfg_get("thresholds", "audio", "window_s", default=1.0),
        hop_s=cfg_get("thresholds", "audio", "hop_s", default=0.5),
        threshold=cfg_get("thresholds", "audio", "prob_threshold", default=0.15),
    )
    t0_wall = datetime.now(timezone.utc)
    for ev in merge_audio_events(det.windows(), subject_id, t0_wall, zone_getter=lambda: shared.zone):
        # the collar mic says whether it was our dog (loud at the collar) or another sound source
        shared.fusion.tag_sound_event(ev, _unix(ev["started_at"]), _unix(ev["ended_at"]))
        post_event(server, ev)


def run_collar_only(server: str, subject_id: str, shared: Shared) -> None:
    """While the dog is out of the camera's view, the collar still reports movement."""
    collar = shared.fusion.collar
    t0 = time.time()
    seg = Segmenter(subject_id, "sensor", datetime.fromtimestamp(t0, timezone.utc), stable_window_s=2.0,
                    max_segment_s=cfg_get("thresholds", "segmenter", "max_segment_s", default=5.0))
    while True:
        time.sleep(1.0)
        now = time.time()
        latest = collar.windows[-1] if collar.windows else None
        moving = latest is not None and latest["t_end"] >= now - 3 and latest["activity"] in GAITS
        dog_off_camera = now - shared.dog_last_seen > 3
        if moving and dog_off_camera:
            ev = seg.push({"t": now - t0, "label": latest["activity"], "confidence": 0.6,
                           "zone": "unknown", "detector": "collar_imu"})
        else:
            ev = seg.flush()
        if ev:
            post_event(server, ev)


# ------------------------------------------------------------------------------------ playback
class _Overlay:
    overlay = None


def run_playback(rec_dir: str, server: str) -> None:
    """Play a recording's video and post its processed events at the right moments, re-timed so the
    dashboard sees them 'live' now (scripts/process_session.py makes <rec>/processed/)."""
    rec = Path(rec_dir)
    proc = rec / "processed"
    events = [json.loads(l) for l in (proc / "events.jsonl").read_text().splitlines() if l.strip()]
    sync = json.loads((proc / "sync.json").read_text())
    rec_t0 = sync["video_t0"]
    events.sort(key=lambda e: e["ended_at"])
    vs = VideoSource(str(rec / sync.get("video_file", "video.mp4")), loop_files=False).start()
    threading.Thread(target=run_display, daemon=True, args=(vs, _Overlay(), server, 0)).start()
    wall0, mono0 = datetime.now(timezone.utc), time.monotonic()
    session = requests.Session()
    print(f"[playback] {rec.name}: {len(events)} events over {sync.get('duration_s', 0):.0f} s")
    i = 0
    while not vs.ended or i < len(events):
        pt = time.monotonic() - mono0  # seconds into the recording
        while i < len(events) and _unix(events[i]["ended_at"]) - rec_t0 <= pt:
            ev = dict(events[i])
            ev["evidence"] = dict(ev["evidence"])
            for k in ("started_at", "ended_at"):
                ev[k] = to_iso(wall0 + timedelta(seconds=_unix(events[i][k]) - rec_t0))
            thumb = proc / "thumbs" / f"{events[i]['event_id']}.jpg"
            if thumb.exists():
                ev["_thumb"] = thumb.read_bytes()
            post_event(server, ev)
            i += 1
        cur = next((e for e in events if e["source"] != "audio"
                    and _unix(e["started_at"]) - rec_t0 <= pt <= _unix(e["ended_at"]) - rec_t0), None)
        det = cur["evidence"].get("detector", "") if cur else ""
        current = None if cur is None else {
            "label": cur["label"], "description": cur["label"], "confidence": cur["confidence"],
            "zone": cur["zone"], "source": "claude" if "vision_strip" in det and "mock" not in det else "estimate"}
        try:
            session.post(f"{server}/api/current", json={"current": current}, timeout=1)
        except Exception:
            pass
        if vs.ended and i >= len(events):
            break
        time.sleep(0.25)
    print("[playback] finished")


# ---------------------------------------------------------------------------------------- main
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
    ap.add_argument("--collar", default=None, help="collar session folder, or the collar's sessions dir")
    ap.add_argument("--collar-offset", type=float, default=0.0,
                    help="seconds to add to collar timestamps (only if the receiver ran on another machine)")
    ap.add_argument("--record", nargs="?", const="", default=None, metavar="NAME",
                    help="also save camera video + mic audio to data/recordings/<NAME or date_time>/")
    ap.add_argument("--playback", default=None, help="play a processed recording in sync")
    args = ap.parse_args()

    if args.playback:
        run_playback(args.playback, args.server)
        return

    subject_id = args.subject_id or cfg_get("subject_id", default="demo_dog_01")
    collar = None
    if args.collar:
        from app.io.collar import CollarTimeline
        collar = CollarTimeline(offset_s=args.collar_offset).follow(args.collar)
        print(f"[run] following collar session {collar.session}")
    shared = Shared(Fusion(collar))

    video_rec = audio_rec = rec_dir = None
    if args.record is not None:
        from app.io.recording import AudioRecorder, VideoRecorder, new_recording_dir, update_meta
        rec_dir = new_recording_dir(args.record or None)
        live_video = args.video is not None and (args.video.isdigit() or "://" in args.video)
        video_rec = VideoRecorder(rec_dir) if live_video else None
        audio_rec = AudioRecorder(rec_dir) if args.audio == "mic" else None
        update_meta(rec_dir, camera=args.video, audio=args.audio,
                    collar_session=str(collar.session) if collar else None, collar_offset_s=args.collar_offset)
        print(f"[run] recording to {rec_dir}  (video: {'yes' if video_rec else 'no'}, "
              f"mic: {'yes' if audio_rec else 'no'})")

    threads = []
    if args.video is not None:
        threads.append(threading.Thread(target=run_video, daemon=True,
                                        args=(args.video, args.server, subject_id, shared, args.loop, video_rec)))
    if args.audio is None and args.video and not args.video.isdigit() and "://" not in args.video:
        from app.io.media import has_audio
        if has_audio(args.video):  # a clip with sound: listen to it too
            args.audio = args.video
            print(f"[run] {args.video} has an audio track -- running the bark/whine detector on it")
    if args.audio is not None:
        threads.append(threading.Thread(target=run_audio, daemon=True,
                                        args=(args.audio, args.server, subject_id, shared, audio_rec)))
    if collar is not None:
        threads.append(threading.Thread(target=run_collar_only, daemon=True,
                                        args=(args.server, subject_id, shared)))

    if not threads:
        print("[run] nothing to do -- pass --video and/or --audio")
        return

    for th in threads:
        th.start()
    try:
        # the collar thread runs forever; the run ends when the camera/file (and audio) finish
        for th in threads[: 2 if collar is None else len(threads) - 1]:
            th.join()
    except KeyboardInterrupt:
        print("\n[run] stopping")
    finally:
        for r in (video_rec, audio_rec):
            if r is not None:
                r.close()
        if rec_dir is not None:
            from app.io.recording import update_meta
            update_meta(rec_dir, ended_at=time.time())
            print(f"[run] recording saved: {rec_dir}")


if __name__ == "__main__":
    main()
