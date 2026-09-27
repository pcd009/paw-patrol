"""Per-day history on disk, so the owner's "today" survives restarts.

    data/history/2026-09-27.jsonl        one JSON object per line: {"kind": "event"|"alert", "data": {...}}
    data/history/thumbs/<event_id>.jpg   picture of the dog at the start of each event

Days are the machine's local calendar days (the owner's day), timestamps inside stay UTC.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "history"
THUMB_DIR = HISTORY_DIR / "thumbs"

_lock = threading.Lock()


def local_today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _local_day(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")


def _day_file(day: str) -> Path:
    return HISTORY_DIR / f"{day}.jsonl"


def append(kind: str, data: dict) -> None:
    """kind: 'event' (keyed by started_at) or 'alert' (keyed by triggered_at)."""
    stamp = data.get("started_at") or data.get("triggered_at")
    day = _local_day(stamp) if stamp else local_today()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, _day_file(day).open("a") as f:
        f.write(json.dumps({"kind": kind, "data": data}) + "\n")


def load_day(day: Optional[str] = None) -> Tuple[List[dict], List[dict]]:
    """(events, alerts) recorded for a local day (default: today)."""
    path = _day_file(day or local_today())
    events, alerts = [], []
    if not path.exists():
        return events, alerts
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        (events if rec.get("kind") == "event" else alerts).append(rec["data"])
    events.sort(key=lambda e: e["started_at"])
    return events, alerts


def save_thumb(event_id: str, jpeg: bytes) -> None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    (THUMB_DIR / f"{event_id}.jpg").write_bytes(jpeg)


def load_thumb(event_id: str) -> Optional[bytes]:
    if "/" in event_id or ".." in event_id:
        return None
    path = THUMB_DIR / f"{event_id}.jpg"
    return path.read_bytes() if path.exists() else None


def has_thumb(event_id: str) -> bool:
    return (THUMB_DIR / f"{event_id}.jpg").exists()
