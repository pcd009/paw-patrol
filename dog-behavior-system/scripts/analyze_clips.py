"""Build today's history from REAL analysis of recorded clips (YOLO + Claude vision + audio).

    .venv/bin/python -m scripts.analyze_clips --replace      # wipe today's history, analyse all clips
    .venv/bin/python -m scripts.analyze_clips --replace --fresh   # ignore cached Claude answers

Each clip in data/demo_videos/ is analysed exactly like live video (YOLO finds the dog, Claude
labels 4-frame strips every 2 s of video, answers are vote-smoothed, the segmenter turns them into
events) and audio tracks go through the bark/whine model. Labels and durations are real; only
*when in the day* each clip happened is staged, from data/demo_videos/day_plan.json (created on
first run -- edit the times). Events carry evidence.clip_id, and the dashboard shows an
"analysed from recorded clips" badge.

Claude answers are cached per clip in data/analysis_cache/, so re-runs cost nothing.
Refuses to run without a working Claude connection unless --allow-mock is given.
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2

from app import history
from app.config import get as cfg_get
from app.io.media import extract_wav, has_audio
from contracts.validate import validate
from services.context_rules.engine import evaluate_rules, resolve_stale
from services.detection.segmenter import Segmenter
from services.detection.video_detector import ROOT, VideoDetector, _padded_crop, _thumb_jpeg
from services.detection.vision_labeler import label_strip
from services.llm_common import CLAUDE_MODEL, is_live

CLIPS = ROOT / "data" / "demo_videos"
PLAN = CLIPS / "day_plan.json"
CACHE = ROOT / "data" / "analysis_cache"
DEFAULT_TIMES = ["07:40", "09:05", "11:15", "13:00", "14:40", "16:05", "17:10", "18:30"]


def load_plan() -> list:
    clips = sorted(p.name for p in CLIPS.glob("*.mp4"))
    plan = json.loads(PLAN.read_text()) if PLAN.exists() else []
    known = {p["clip"] for p in plan}
    for name in clips:  # new clips get the next free default slot
        if name not in known:
            used = {p["at"] for p in plan}
            at = next((t for t in DEFAULT_TIMES if t not in used), "19:00")
            plan.append({"clip": name, "at": at})
    plan = [p for p in plan if (CLIPS / p["clip"]).exists()]
    plan.sort(key=lambda p: p["at"])
    PLAN.write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def claude_answers(path: Path, fresh: bool) -> list:
    """[(t, label, conf, desc)] from Claude on 4-frame strips every `interval` s of video. Cached."""
    vis = cfg_get("thresholds", "vision_labeler", default={})
    interval, span, n_frames = vis.get("interval_s", 2.0), vis.get("strip_span_s", 1.2), vis.get("strip_frames", 4)
    key = f"{path.stem}_{path.stat().st_size}_{CLAUDE_MODEL}_{interval}_{n_frames}.json"
    cache_file = CACHE / key
    if cache_file.exists() and not fresh:
        return json.loads(cache_file.read_text())

    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")  # fresh tracker per clip
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / 8))
    crops: deque = deque(maxlen=60)
    answers, next_call, idx = [], span, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            t = idx / fps
            box = VideoDetector._largest_dog_box(model, frame)
            if box is not None:
                crops.append((t, _padded_crop(frame, box[:4])))
            if crops and t >= next_call and t - crops[0][0] >= span * 0.8:
                t_end = crops[-1][0]
                picks = [min(crops, key=lambda c: abs(c[0] - (t_end - span * (n_frames - 1 - k) / (n_frames - 1))))
                         for k in range(n_frames)]
                picks = list({id(p): p for p in picks}.values())
                label, conf, used, desc = label_strip([c for _, c in picks], [pt for pt, _ in picks])
                answers.append([round(t, 2), label, round(conf, 3), desc, used])
                next_call = t + interval
        idx += 1
    CACHE.mkdir(parents=True, exist_ok=True)
    if answers and all(a[4] != "mock" for a in answers):
        cache_file.write_text(json.dumps(answers))
    return answers


def video_events(path: Path, start: datetime, answers: list) -> list:
    """Same smoothing + segmentation as live, with the clip's thumbnails."""
    vote_n = int(cfg_get("thresholds", "vision_labeler", "vote_over", default=3))
    voted, history_ = [], []
    for t, label, conf, desc, used in answers:  # confidence-weighted vote over the last N answers
        history_ = (history_ + [(label, conf, desc)])[-vote_n:]
        score = {}
        for l, c, _ in history_:
            score[l] = score.get(l, 0.0) + c
        win = max(score, key=score.get)
        mine = [h for h in history_ if h[0] == win]
        voted.append((t, win, sum(h[1] for h in mine) / len(mine), used))
    if not voted:
        return []

    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
    seg = Segmenter("demo_dog_01", "video", start.astimezone(timezone.utc),
                    stable_window_s=cfg_get("thresholds", "segmenter", "stable_window_s", default=1.0),
                    max_segment_s=cfg_get("thresholds", "segmenter", "max_segment_s", default=5.0))
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / 8))
    events, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            t = idx / fps
            box = VideoDetector._largest_dog_box(model, frame)
            if box is not None:
                # label for this moment = the (smoothed) Claude answer nearest in time
                vt, label, conf, used = min(voted, key=lambda v: abs(v[0] - t))
                ev = seg.push({"t": t, "label": label, "confidence": conf, "zone": "unknown",
                               "detector": f"yolo11n+vision_strip:{used}",
                               "thumb": _thumb_jpeg(frame, box[:4])})
                if ev:
                    events.append(ev)
        idx += 1
    last = seg.flush()
    if last:
        events.append(last)
    return events


def audio_events(path: Path, start: datetime) -> list:
    if not has_audio(str(path)):
        return []
    from app.io.inputs import AudioSource
    from services.detection.audio_detector import AudioDetector, merge_events
    src = AudioSource(extract_wav(str(path)), realtime=False)
    det = AudioDetector(src, window_s=cfg_get("thresholds", "audio", "window_s", default=1.0),
                        hop_s=cfg_get("thresholds", "audio", "hop_s", default=0.5),
                        threshold=cfg_get("thresholds", "audio", "prob_threshold", default=0.15))
    return list(merge_events(det.windows(), "demo_dog_01", start.astimezone(timezone.utc)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="wipe today's history first")
    ap.add_argument("--fresh", action="store_true", help="ignore cached Claude answers")
    ap.add_argument("--allow-mock", action="store_true", help="run even without Claude (rough labels)")
    args = ap.parse_args()

    if not is_live() and not args.allow_mock:
        raise SystemExit("No Claude connection (ANTHROPIC_API_KEY / .env). Export the key in this terminal, "
                         "or pass --allow-mock for rough heuristic labels.")
    day_file = history.HISTORY_DIR / f"{history.local_today()}.jsonl"
    if day_file.exists():
        if not args.replace:
            raise SystemExit(f"{day_file.name} already has history -- rerun with --replace to overwrite it")
        day_file.unlink()

    now = datetime.now().astimezone()
    plan = load_plan()
    print(f"Plan ({PLAN.relative_to(ROOT)}):  " + ",  ".join(f"{p['at']} {p['clip']}" for p in plan))
    all_events = []
    for p in plan:
        h, m = (int(x) for x in p["at"].split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        path = CLIPS / p["clip"]
        if start > now:
            print(f"  skip {p['clip']}: scheduled at {p['at']}, which is later than now")
            continue
        answers = claude_answers(path, args.fresh)
        vids = video_events(path, start, answers)
        auds = audio_events(path, start)
        for ev in vids + auds:
            thumb = ev.pop("_thumb", None)
            offset = (datetime.fromisoformat(ev["started_at"].replace("Z", "+00:00")) - start).total_seconds()
            dur = (datetime.fromisoformat(ev["ended_at"].replace("Z", "+00:00"))
                   - datetime.fromisoformat(ev["started_at"].replace("Z", "+00:00"))).total_seconds()
            ev["evidence"].update({"clip_id": path.stem, "clip_start_ms": int(offset * 1000),
                                   "clip_end_ms": int((offset + dur) * 1000)})
            validate(ev, "behavior_event")
            all_events.append((ev, thumb))
        labels = [e["label"] for e in vids]
        print(f"  {p['at']}  {p['clip']:<28} {len(answers)} Claude answers -> {len(vids)} video events "
              f"({', '.join(dict.fromkeys(labels)) or 'none'}), {len(auds)} audio events")

    all_events.sort(key=lambda x: x[0]["started_at"])
    events = [e for e, _ in all_events]
    alerts = []
    for i in range(len(events)):  # the real rule engine, replayed in order
        alerts.extend(evaluate_rules(events[: i + 1], alerts))
    resolve_stale(alerts, now.astimezone(timezone.utc))
    for ev, thumb in all_events:
        history.append("event", ev)
        if thumb:
            history.save_thumb(ev["event_id"], thumb)
    for a in alerts:
        history.append("alert", a)
    print(f"Wrote {len(events)} events and {len(alerts)} alerts to {day_file.name}. Restart the server to load them.")


if __name__ == "__main__":
    main()
