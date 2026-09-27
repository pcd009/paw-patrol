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
