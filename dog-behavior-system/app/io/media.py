"""Audio from video files, using the ffmpeg binary bundled by the imageio-ffmpeg package
(no system ffmpeg needed)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def has_audio(path: str) -> bool:
    """True if the media file has an audio stream."""
    try:
        out = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=20)
        return "Audio:" in out.stderr
    except Exception:
        return False


def extract_wav(path: str, sample_rate: int = 16000) -> str:
    """Decode the audio track to a mono wav in a temp file; returns its path."""
    out = Path(tempfile.gettempdir()) / f"pawpatrol_{Path(path).stem}_{sample_rate}.wav"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                    "-vn", "-ac", "1", "-ar", str(sample_rate), str(out)], check=True, timeout=120)
    return str(out)


def video_start_time(path: str):
    """(Unix time the recording started, where it came from) from the file's metadata, or (None, None).

    iPhone / Mac (QuickTime) files carry `com.apple.quicktime.creationdate` -- local time with the
    timezone, the moment recording started -- and `creation_time` (UTC). Both have ~1 s precision
    and use the recording device's clock, so refine with a clap for sub-second sync.
    """
    import re
    from datetime import datetime
    try:
        err = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=20).stderr
    except Exception:
        return None, None
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        m = re.search(rf"{re.escape(key)}\s*:\s*(\S+)", err)
        if not m:
            continue
        stamp = m.group(1).replace("Z", "+00:00")
        stamp = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", stamp)  # +0530 -> +05:30
        try:
            dt = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.astimezone()  # no zone given: treat as this machine's local time
        return dt.timestamp(), key
    return None, None
