"""Check the vision labeler against tagged clips: data/demo_videos/<tag>-<n>.mp4.

    .venv/bin/python -m scripts.eval_clips            # 6 samples per clip
    .venv/bin/python -m scripts.eval_clips --samples 10

For each sample it takes 3 frames spanning ~1 s, crops the dog with YOLO (same as live), asks the
labeler, and compares with the tag in the filename. Uses the real Claude model if the API key is
available (shell export or .env), otherwise the mock -- the header says which.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from services.detection.video_detector import ROOT, DOG_CLASS_ID, _padded_crop
from services.detection.vision_labeler import label_strip
from services.llm_common import CLAUDE_MODEL, is_live

# tag in filename -> labels that count as correct
ACCEPT = {
    "running": {"galloping", "trotting"},
    "walking": {"walking"},
    "trotting": {"trotting"},
    "galloping": {"galloping"},
    "sitting": {"sitting"},
    "standing": {"standing"},
    "lying": {"lying_on_chest"},
    "sniffing": {"sniffing"},
    "barking": {"barking"},
}


def dog_crop(model, frame):
    # plain detection (not tracking): samples jump around in time and across clips
    r = model.predict(frame, classes=[DOG_CLASS_ID], verbose=False)[0]
    if r.boxes is None or len(r.boxes) == 0:
        return None
    boxes = r.boxes.xyxy.cpu().numpy()
    i = int(((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])).argmax())
    return _padded_crop(frame, boxes[i])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=6)
    args = ap.parse_args()

    from ultralytics import YOLO
    weights = ROOT / "models" / "yolo11n.pt"
    model = YOLO(str(weights) if weights.exists() else "yolo11n.pt")
    print(f"labeler: {'Claude (' + CLAUDE_MODEL + ')' if is_live() else 'MOCK -- no API key found'}\n")

    total = correct = 0
    for path in sorted((ROOT / "data" / "demo_videos").glob("*.mp4")):
        tag = path.stem.split("-")[0].lower()
        if tag not in ACCEPT:
            continue
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        span = int(fps)  # ~1 s of frames per sample
        hits = []
        for k in range(args.samples):
            end = int(span + (n - span - 1) * (k + 0.5) / args.samples)
            crops, times = [], []
            for idx in (end - span, end - span // 2, end):
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                crop = dog_crop(model, frame) if ok else None
                if crop is not None:
                    crops.append(crop)
                    times.append(idx / fps)
            if not crops:
                hits.append(("no dog", None, False))
                continue
            label, conf, _model, desc = label_strip(crops, times)
            ok = label in ACCEPT[tag]
            hits.append((desc, conf, ok))
        clip_ok = sum(h[2] for h in hits)
        total += len(hits)
        correct += clip_ok
        print(f"{path.name}  (expect {tag}): {clip_ok}/{len(hits)} correct")
        for i, (desc, conf, ok) in enumerate(hits):
            c = f"{conf:.2f}" if conf is not None else "  - "
            print(f"   {'✓' if ok else '✗'} sample {i + 1}: {desc:<28} conf {c}")
    if total:
        print(f"\nOverall: {correct}/{total} = {100 * correct / total:.0f}%")


if __name__ == "__main__":
    main()
