"""Tiny shared helpers so every module formats IDs and timestamps the same way."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"


def new_id(prefix: str) -> str:
    """new_id("evt") -> "evt_3f9a1c2b7d4e"."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def to_iso(dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds and a trailing Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def parse_ts(s: str) -> datetime:
    # Python 3.9's fromisoformat does not accept a trailing "Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
