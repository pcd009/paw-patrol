"""Record the laptop camera + mic with Unix timestamps -- no analysis, no server. Run it next to
the collar receiver (which records the gyro + collar mic on the same clock), then process both:

    .venv/bin/python -m scripts.record --name dog1                      # camera 0 + mic
    .venv/bin/python -m scripts.record --name dog1 --collar ../paw-patrol/sessions   # + shows collar status
    ...Ctrl-C to stop...
    .venv/bin/python -m scripts.process_session data/recordings/dog1 --collar ../paw-patrol/sessions/<s>

Tip: at the start, clap once near the collar while it's in view of the camera. If the collar receiver
runs on another machine, `process_session --auto-sync` uses that clap to line up the clocks.
"""
from __future__ import annotations

import argparse
import threading
import time

from app.io.inputs import AudioSource, VideoSource
from app.io.recording import AudioRecorder, VideoRecorder, new_recording_dir, update_meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=None, help="folder name under data/recordings/ (default: date_time)")
    ap.add_argument("--camera", default="0", help="webcam index or rtsp/http URL")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--collar", default=None, help="collar session/sessions dir, to show whether it's recording too")
    args = ap.parse_args()

    d = new_recording_dir(args.name)
    vrec = VideoRecorder(d)
    arec = None if args.no_audio else AudioRecorder(d)
    update_meta(d, camera=args.camera, audio=None if args.no_audio else "mic", collar_session=args.collar)
    vs = VideoSource(args.camera, recorder=vrec, max_width=1280).start()
    if arec is not None:
        src = AudioSource("mic", recorder=arec)
        threading.Thread(target=lambda: [None for _ in src.chunks()], daemon=True).start()

    print(f"Recording to {d}\n  clap once near the collar in view of the camera to mark the sync point. Ctrl-C to stop.")
    t0 = time.time()
    try:
        while not vs.ended:
            time.sleep(5)
            collar = ""
            if args.collar:
                from app.io.collar import read_live, resolve_session
                try:
                    live = read_live(resolve_session(args.collar))
                    age = time.time() - live["t_laptop"] if live else None
                    collar = "  collar: " + (f"live ({age:.1f}s old)" if age is not None and age < 3 else "NOT receiving")
                except Exception:
                    collar = "  collar: no session found"
            secs = arec.samples / arec.sample_rate if arec else 0
            print(f"  {time.time() - t0:5.0f}s  video {vrec.frames} frames ({vs.native_fps:.0f} fps)  audio {secs:.0f}s{collar}")
    except KeyboardInterrupt:
        pass
    finally:
        vrec.close()
        if arec is not None:
            arec.close()
        update_meta(d, ended_at=time.time())
        print(f"\nSaved {d}")


if __name__ == "__main__":
    main()
