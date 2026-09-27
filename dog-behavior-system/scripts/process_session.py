"""Process a whole recorded session -- laptop video + mic, synced with a collar session -- into events.

    .venv/bin/python -m scripts.process_session data/recordings/<name> --collar ../paw-patrol/sessions/<s>
    .venv/bin/python -m scripts.process_session IMG_1234.MOV --collar ../paw-patrol/sessions/dog1
    .venv/bin/python -m scripts.process_session IMG_1234.MOV --collar ... --check-sync   # just show the sync

Sync, in two steps:
  1. coarse -- the video's start time: iPhone/Mac files carry it in their metadata (read automatically;
     ~1 s precision, phone clock) or give --video-start; our own recordings have per-frame Unix times.
     Frame times come from each frame's own timestamp (phones record at a variable frame rate).
  2. fine  -- the collar laptop and the phone have different clocks. At the start of the session,
     press collar button A while clapping once in front of the camera: `--sync clap` (the default
     when the collar session has a marker) finds the clap in the video's sound and lines it up with
     the marker. Or `--sync mic` (correlate the collar mic with the video sound), or `--offset S`.

Pipeline (same logic as live): YOLO finds the dog -> Claude labels 4-frame strips every 2 s ->
vote smoothing -> collar fusion (gyro gait, "still" correction) -> segmenter. Laptop audio -> bark /
whine model -> collar mic says our dog or not. Collar-only movement while the dog is off camera.
Rules run on the result. Output: <rec>/processed/ events.jsonl, alerts.jsonl, thumbs/, sync.json.
Play it back in sync on the dashboard:  scripts/run_demo.sh playback <rec>
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.config import get as cfg_get
from contracts.validate import validate
from services.context_rules.engine import evaluate_rules, resolve_stale
from services.detection.segmenter import Segmenter
from services.detection.video_detector import ROOT, VideoDetector, _padded_crop, _thumb_jpeg
from services.detection.vision_labeler import label_strip
from services.fusion import GAITS, Fusion
from services.llm_common import CLAUDE_MODEL, is_live

SUBJECT = cfg_get("subject_id", default="demo_dog_01")


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, timezone.utc)


def _unix(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ----------------------------------------------------------------------------------- inputs
def open_inputs(src: Path, video_start: str | None):
    """-> (out_dir, video_path, frame_time(idx, pos_ms)->unix, audio_path|None, audio_t0|None, start_note)."""
    if src.is_dir():
        video = src / "video.mp4"
        ts = [float(l.split(",")[1]) for l in (src / "frames.csv").read_text().splitlines()[1:] if "," in l]
        frame_time = lambda i, pos_ms=None: ts[min(i, len(ts) - 1)]
        audio, audio_t0 = None, None
        if (src / "audio.wav").exists() and (src / "audio.json").exists():
            audio, audio_t0 = src / "audio.wav", json.loads((src / "audio.json").read_text())["t_start"]
        return src, video, frame_time, audio, audio_t0, "per-frame Unix times (frames.csv)"
    if video_start:
        try:
            t0 = float(video_start)
        except ValueError:
            t0 = datetime.fromisoformat(video_start).astimezone().timestamp()
        note = "--video-start"
    else:
        from app.io.media import video_start_time
        t0, key = video_start_time(str(src))
        if t0 is None:
            raise SystemExit("no start time in this video's metadata -- give --video-start "
                             '(e.g. "2026-09-27 15:02:10", local time, or a Unix time)')
        note = f"file metadata ({key})"
    fps = cv2.VideoCapture(str(src)).get(cv2.CAP_PROP_FPS) or 30.0
    out = ROOT / "data" / "recordings" / src.stem
    out.mkdir(parents=True, exist_ok=True)
    from app.io.media import has_audio
    audio = src if has_audio(str(src)) else None
    # each frame's own timestamp (pos_ms) when known -- phones record at a variable frame rate
    frame_time = lambda i, pos_ms=None: t0 + (pos_ms / 1000.0 if pos_ms is not None and pos_ms > 0 else i / fps)
    return out, src, frame_time, audio, (t0 if audio else None), note


# ------------------------------------------------------------------------------------- sync
def _wav(path) -> str:
    """Audio as a wav path (video files are decoded with the bundled ffmpeg)."""
    if str(path).lower().endswith(".wav"):
        return str(path)
    from app.io.media import extract_wav
    return extract_wav(str(path))


def auto_offset(audio_path, audio_t0, collar_session, max_lag_s: float = 10.0):
    """Offset (s) to add to collar times so the collar mic lines up with the laptop mic -- works best
    with a sharp clap near the collar at the start. Returns (offset, confidence 0..1)."""
    import librosa
    from app.io.collar import load_imu, resolve_session
    rows = load_imu(resolve_session(collar_session))
    if not rows or audio_path is None:
        return 0.0, 0.0
    y, sr = librosa.load(_wav(audio_path), sr=16000, mono=True)
    hop = sr // 50  # 50 Hz envelope, like the collar mic
    env = np.array([np.abs(y[i:i + hop]).max() for i in range(0, len(y) - hop, hop)])
    lap_t = audio_t0 + np.arange(len(env)) / 50.0
    col_t = np.array([r["t_laptop"] for r in rows])
    col = np.array([r["mic"] for r in rows], dtype=float)
    grid = np.arange(max(lap_t[0], col_t[0] - max_lag_s), min(lap_t[-1], col_t[-1] + max_lag_s), 0.02)
    if len(grid) < 100:
        return 0.0, 0.0
    a = np.interp(grid, lap_t, env)
    b = np.interp(grid, col_t, col)
    a, b = (a - a.mean()) / (a.std() or 1), (b - b.mean()) / (b.std() or 1)
    lags = np.arange(-int(max_lag_s * 50), int(max_lag_s * 50) + 1)
    def score(k: int) -> float:  # mean of a[j] * b[j + k] over the overlapping part
        x, y = (a[: len(a) - k], b[k:]) if k >= 0 else (a[-k:], b[: len(b) + k])
        m = min(len(x), len(y))
        return float(np.mean(x[:m] * y[:m])) if m > 50 else -np.inf

    scores = np.array([score(int(k)) for k in lags])
    best = int(scores.argmax())
    # collar sample at grid[j + k] matches laptop at grid[j] -> collar is ahead by k/50 s
    offset = -lags[best] / 50.0
    # confidence: how much the best lag stands out from the best lag >0.5 s away from it
    others = scores[np.abs(np.arange(len(scores)) - best) > 25]
    others = others[np.isfinite(others)]
    peak = scores[best]
    conf = float(np.clip((peak - others.max()) / (abs(peak) + 1e-9), 0, 1)) if len(others) and np.isfinite(peak) else 0.0
    return offset, conf


def clap_offset(audio_path, audio_t0, collar_session, window_s: float = 20.0):
    """Offset (s) to add to collar times, from ONE shared moment: collar button A pressed while
    clapping in front of the camera. Finds the sharpest sound onset in the video's audio near the
    marker. Without a marker, uses the collar mic's loudest spike instead.
    Returns (offset, description) or (None, reason)."""
    import librosa
    from app.io.collar import load_events, load_imu, resolve_session
    if audio_path is None:
        return None, "the video has no sound to find the clap in"
    sess = resolve_session(collar_session)
    markers = [e["t"] for e in load_events(sess) if e.get("type") == "marker"]
    if markers:
        anchor, what = markers[0], "collar button-A marker"
    else:
        rows = load_imu(sess)[: 120 * 50]
        if not rows:
            return None, "the collar session has no samples"
        peak = max(rows, key=lambda r: r["mic"])
        anchor, what = peak["t_laptop"], f"loudest collar-mic spike (mic={peak['mic']})"
    y, sr = librosa.load(_wav(audio_path), sr=16000, mono=True)
    hop = sr // 100  # 10 ms envelope
    env = np.array([np.abs(y[i:i + hop]).max() for i in range(0, len(y) - hop, hop)])
    onset = np.maximum(np.diff(env, prepend=env[0]), 0)
    t = audio_t0 + np.arange(len(env)) / 100.0
    near = (t >= anchor - window_s) & (t <= anchor + window_s)
    if not near.any():
        return None, (f"the {what} ({datetime.fromtimestamp(anchor):%H:%M:%S}) is not within {window_s:.0f} s of "
                      f"the video (starts {datetime.fromtimestamp(audio_t0):%H:%M:%S}) -- check the start time or "
                      f"use --sync-window / --offset")
    i = int(np.flatnonzero(near)[onset[near].argmax()])
    return float(t[i] - anchor), f"clap at {t[i] - audio_t0:.2f} s into the video <-> {what}"


# ------------------------------------------------------------------------------------ video
def claude_answers(video: Path, frame_time, cache: Path, fresh: bool):
    """[(t_unix, label, conf, desc, model)] from Claude on 4-frame dog strips every interval_s."""
    vis = cfg_get("thresholds", "vision_labeler", default={})
    interval, span, n = vis.get("interval_s", 2.0), vis.get("strip_span_s", 1.2), vis.get("strip_frames", 4)
    if cache.exists() and not fresh:
        c = json.loads(cache.read_text())
        if c.get("model") == CLAUDE_MODEL:
            return c["answers"]
    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
    cap = cv2.VideoCapture(str(video))
    step = max(1, round((cap.get(cv2.CAP_PROP_FPS) or 30.0) / 8))
    crops, answers, next_call, idx = deque(maxlen=60), [], None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            t = frame_time(idx, cap.get(cv2.CAP_PROP_POS_MSEC))
            box = VideoDetector._largest_dog_box(model, frame)
            if box is not None:
                crops.append((t, _padded_crop(frame, box[:4])))
            if next_call is None and crops:
                next_call = crops[0][0] + span
            if crops and next_call is not None and t >= next_call and t - crops[0][0] >= span * 0.8:
                t_end = crops[-1][0]
                picks = [min(crops, key=lambda c: abs(c[0] - (t_end - span * (n - 1 - k) / (n - 1)))) for k in range(n)]
                picks = list({id(p): p for p in picks}.values())
                label, conf, used, desc = label_strip([c for _, c in picks], [pt for pt, _ in picks])
                answers.append([t, label, conf, desc, used])
                next_call = t + interval
                if len(answers) % 10 == 0:
                    print(f"    ... {len(answers)} Claude answers ({t - frame_time(0):.0f} s into the video)")
        idx += 1
    if answers and all(a[4] != "mock" for a in answers):
        cache.write_text(json.dumps({"model": CLAUDE_MODEL, "answers": answers}))
    return answers


def video_events(video: Path, frame_time, answers, fusion: Fusion):
    """-> (events with _thumb, list of unix times the dog was visible)."""
    vote_n = int(cfg_get("thresholds", "vision_labeler", "vote_over", default=3))
    voted, hist = [], []
    for t, label, conf, desc, used in answers:
        hist = (hist + [(label, conf)])[-vote_n:]
        score = {}
        for l, c in hist:
            score[l] = score.get(l, 0.0) + c
        win = max(score, key=score.get)
        mine = [c for l, c in hist if l == win]
        voted.append((t, win, sum(mine) / len(mine), used))
    if not voted:
        return [], []
    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
    t0 = frame_time(0)
    seg = Segmenter(SUBJECT, "video", _dt(t0),
                    stable_window_s=cfg_get("thresholds", "segmenter", "stable_window_s", default=1.0),
                    max_segment_s=cfg_get("thresholds", "segmenter", "max_segment_s", default=5.0))
    cap = cv2.VideoCapture(str(video))
    step = max(1, round((cap.get(cv2.CAP_PROP_FPS) or 30.0) / 8))
    events, seen, idx = [], [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            t = frame_time(idx, cap.get(cv2.CAP_PROP_POS_MSEC))
            box = VideoDetector._largest_dog_box(model, frame)
            if box is not None:
                seen.append(t)
                _, label, conf, used = min(voted, key=lambda v: abs(v[0] - t))
                detector = f"yolo11n+vision_strip:{used}"
                label, note = fusion.video_label(label, t)
                if note:
                    detector += f"+{note}"
                ev = seg.push({"t": t - t0, "label": label, "confidence": conf, "zone": "unknown",
                               "detector": detector, "thumb": _thumb_jpeg(frame, box[:4])})
                if ev:
                    events.append(ev)
        idx += 1
    last = seg.flush()
    if last:
        events.append(last)
    return events, seen


# ------------------------------------------------------------------------------------ audio
def audio_events(audio, audio_t0, fusion: Fusion):
    if audio is None:
        return []
    from app.io.inputs import AudioSource
    from services.detection.audio_detector import AudioDetector, merge_events
    det = AudioDetector(AudioSource(str(audio), realtime=False),
                        window_s=cfg_get("thresholds", "audio", "window_s", default=1.0),
                        hop_s=cfg_get("thresholds", "audio", "hop_s", default=0.5),
                        threshold=cfg_get("thresholds", "audio", "prob_threshold", default=0.15))
    out = []
    for ev in merge_events(det.windows(), SUBJECT, _dt(audio_t0)):
        out.append(fusion.tag_sound_event(ev, _unix(ev["started_at"]), _unix(ev["ended_at"])))
    return out


def collar_only_events(collar, seen, t_range):
    """Gait segments from the collar while the dog wasn't visible on camera (inside the recording)."""
    if collar is None:
        return []
    from contracts.common import SCHEMA_VERSION, new_id, to_iso
    seen = np.array(sorted(seen)) if seen else np.array([])
    out = []
    for s in collar.segments():
        if s["activity"] not in GAITS or s["t_end"] < t_range[0] or s["t_start"] > t_range[1]:
            continue
        near = seen[(seen >= s["t_start"] - 3) & (seen <= s["t_end"] + 3)] if len(seen) else []
        if len(near):
            continue  # the camera saw the dog: the fused video events already cover this
        out.append({"schema_version": SCHEMA_VERSION, "event_id": new_id("evt"), "subject_id": SUBJECT,
                    "source": "sensor", "label": s["activity"], "confidence": 0.6,
                    "started_at": to_iso(_dt(s["t_start"])), "ended_at": to_iso(_dt(s["t_end"])),
                    "zone": "unknown", "evidence": {"detector": "collar_imu"}})
    return out


# ------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording", help="data/recordings/<name> (from --record) or a video file")
    ap.add_argument("--video-start", help="for a plain video file: when it started (laptop clock)")
    ap.add_argument("--collar", help="collar session folder (or the collar's sessions dir)")
    ap.add_argument("--offset", type=float, default=None, help="seconds added to collar times (manual sync)")
    ap.add_argument("--sync", choices=["auto", "clap", "mic", "clock"], default="auto",
                    help="auto: clap if the collar has a marker, else clock. clap: button A + clap. "
                         "mic: correlate collar mic with the video sound. clock: trust both clocks as they are")
    ap.add_argument("--sync-window", type=float, default=20.0, help="how far (s) to search for the clap")
    ap.add_argument("--auto-sync", action="store_true", help=argparse.SUPPRESS)  # old name for --sync mic
    ap.add_argument("--check-sync", action="store_true", help="only report how the recordings line up")
    ap.add_argument("--fresh", action="store_true", help="ignore cached Claude answers")
    ap.add_argument("--allow-mock", action="store_true", help="run without Claude (rough labels)")
    args = ap.parse_args()

    if args.auto_sync:
        args.sync = "mic"
    if not is_live() and not args.allow_mock and not args.check_sync:
        raise SystemExit("No Claude connection (ANTHROPIC_API_KEY / .env) -- export the key, or pass --allow-mock.")
    src = Path(args.recording).expanduser().resolve()
    out_dir, video, frame_time, audio, audio_t0, start_note = open_inputs(src, args.video_start)
    proc = out_dir / "processed"
    (proc / "thumbs").mkdir(parents=True, exist_ok=True)
    n_frames = int(cv2.VideoCapture(str(video)).get(cv2.CAP_PROP_FRAME_COUNT))
    v0, v1 = frame_time(0), frame_time(max(0, n_frames - 1))
    print(f"Video  {video.name}: {n_frames} frames, {v1 - v0:.0f} s, starts {datetime.fromtimestamp(v0):%Y-%m-%d %H:%M:%S}"
          f"  [{start_note}]")
    print(f"Audio  {'none' if audio is None else Path(audio).name}")

    collar, offset, method = None, 0.0, "none"
    if args.collar:
        from app.io.collar import CollarTimeline, resolve_session
        from app.io.collar import load_events
        has_marker = any(e.get("type") == "marker" for e in load_events(resolve_session(args.collar)))
        mode = args.sync if args.sync != "auto" else ("clap" if has_marker else "clock")
        if args.offset is not None:
            offset, method = args.offset, "manual --offset"
        elif mode == "clap":
            off, why = clap_offset(audio, audio_t0, args.collar, args.sync_window)
            if off is None:
                print(f"  clap sync failed: {why}. Falling back to the clocks as they are.")
                method = "clocks as-is (clap sync failed)"
            else:
                offset, method = off, f"clap: {why}"
        elif mode == "mic":
            offset, conf = auto_offset(audio, audio_t0, args.collar, max_lag_s=args.sync_window)
            method = f"collar mic vs video sound (confidence {conf:.2f})"
            if conf < 0.1:
                print(f"  mic sync is unsure (confidence {conf:.2f}); consider a clap + button A, or --offset")
        else:
            method = "clocks as-is (no marker; press button A while clapping for exact sync)"
        collar = CollarTimeline.load(args.collar, offset_s=offset)
        c0, c1 = (collar.windows[0]["t_start"], collar.windows[-1]["t_end"]) if collar.windows else (0.0, 0.0)
        overlap = max(0.0, min(v1, c1) - max(v0, c0))
        print(f"Collar {resolve_session(args.collar).name}: {c1 - c0:.0f} s, offset {offset:+.2f} s ({method}), "
              f"overlaps the video for {overlap:.0f} s")
        if overlap < 5:
            print("  WARNING: collar and video barely overlap in time -- check the start time / offset")
        print(f"  collar {datetime.fromtimestamp(c0):%H:%M:%S}-{datetime.fromtimestamp(c1):%H:%M:%S} vs video "
              f"{datetime.fromtimestamp(v0):%H:%M:%S}-{datetime.fromtimestamp(v1):%H:%M:%S} (after the offset)")
    if args.check_sync:
        return
    fusion = Fusion(collar)

    print("Labelling video with Claude...")
    answers = claude_answers(video, frame_time, proc / "claude_answers.json", args.fresh)
    vids, seen = video_events(video, frame_time, answers, fusion)
    auds = audio_events(audio, audio_t0, fusion)
    cols = collar_only_events(collar, seen, (v0, v1))

    events = []
    for ev in vids + auds + cols:
        thumb = ev.pop("_thumb", None)
        validate(ev, "behavior_event")
        if thumb:
            (proc / "thumbs" / f"{ev['event_id']}.jpg").write_bytes(thumb)
        events.append(ev)
    events.sort(key=lambda e: e["started_at"])
    alerts = []
    for i in range(len(events)):
        alerts.extend(evaluate_rules(events[: i + 1], alerts))
    resolve_stale(alerts, _dt(v1))

    (proc / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (proc / "alerts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in alerts))
    (proc / "sync.json").write_text(json.dumps({
        "video_file": str(video) if video.parent != out_dir else video.name, "video_t0": v0, "duration_s": v1 - v0,
        "audio_file": str(audio) if audio else None, "audio_t0": audio_t0,
        "collar_session": str(collar.session) if collar else None, "collar_offset_s": offset, "sync_method": method,
        "model": CLAUDE_MODEL if is_live() else "mock", "processed_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))

    fused = sum("collar_" in e["evidence"].get("detector", "") for e in vids)
    ours = sum("our_dog" in e["evidence"].get("detector", "") and "not_our" not in e["evidence"]["detector"] for e in auds)
    print(f"\n{len(answers)} Claude answers -> {len(vids)} video events ({fused} corrected by the collar), "
          f"{len(auds)} sound events ({ours} our dog), {len(cols)} collar-only movement events, {len(alerts)} alerts")
    print(f"Saved {proc}.\nPlay it back:  scripts/run_demo.sh playback {out_dir}")


if __name__ == "__main__":
    main()
