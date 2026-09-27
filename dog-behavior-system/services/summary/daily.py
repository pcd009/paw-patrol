"""Turns today's events into what an owner wants to see at a glance.

Pure function: compute_summary(events, alerts, now, profile) -> dict. No LLM here; the
Claude-written "today" story (digest.py) is built from this summary, not from raw events.

Owner categories (raw contract labels -> what the owner sees):
    resting   <- lying_on_chest
    calm      <- sitting, standing          (awake, not doing much)
    walking   <- walking
    running   <- trotting, galloping        (play / zoomies)
    exploring <- sniffing
Sounds (barking, whining) and the door button are counted, not timed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

CATEGORIES = {
    "resting": {"name": "Resting", "emoji": "😴", "color": "#6c8cff", "labels": {"lying_on_chest"}},
    "calm": {"name": "Calm & awake", "emoji": "🐶", "color": "#9aa3b8", "labels": {"sitting", "standing"}},
    "walking": {"name": "Walking", "emoji": "🐾", "color": "#3ecf9a", "labels": {"walking"}},
    "running": {"name": "Running & play", "emoji": "🏃", "color": "#f5b53d", "labels": {"trotting", "galloping"}},
    "exploring": {"name": "Exploring", "emoji": "👃", "color": "#c77dff", "labels": {"sniffing"}},
}
LABEL_TO_CAT = {l: k for k, c in CATEGORIES.items() for l in c["labels"]}
ACTIVE_CATS = {"walking", "running", "exploring"}


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def _dur(e: dict) -> float:
    return max(0.0, (_ts(e["ended_at"]) - _ts(e["started_at"])).total_seconds())


def _fmt(secs: float) -> str:
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    h, m = divmod(secs // 60, 60)
    return (f"{h}h {m}m" if m else f"{h}h") if h else f"{m} min"


def _groups(events: List[dict]) -> List[dict]:
    """Consecutive same-category events merged (blips < 20 s of another category absorbed)."""
    timed = [e for e in events if e["label"] in LABEL_TO_CAT]
    groups: List[dict] = []
    for e in timed:
        cat = LABEL_TO_CAT[e["label"]]
        if groups and groups[-1]["cat"] == cat:
            g = groups[-1]
            g["end"] = max(g["end"], _ts(e["ended_at"]))
            g["seconds"] += _dur(e)
            continue
        # absorb a short blip sandwiched between two runs of the same category
        if len(groups) >= 2 and groups[-2]["cat"] == cat and groups[-1]["seconds"] < 20:
            blip = groups.pop()
            g = groups[-1]
            g["end"] = max(g["end"], _ts(e["ended_at"]))
            g["seconds"] += blip["seconds"] + _dur(e)
            continue
        groups.append({"cat": cat, "start": _ts(e["started_at"]), "end": _ts(e["ended_at"]),
                       "seconds": _dur(e), "first_event": e["event_id"]})
    return groups


def compute_summary(events: List[dict], alerts: List[dict], now: Optional[datetime] = None,
                    profile: Optional[dict] = None, has_thumb=lambda _id: False,
                    since: Optional[datetime] = None) -> dict:
    """`since`: when monitoring started (live sessions); the day timeline starts there."""
    now = (now or datetime.now()).astimezone()
    profile = profile or {}
    events = sorted(events, key=lambda e: e["started_at"])
    goal_min = int(profile.get("daily_activity_goal_min", 60))

    # --- time per category
    by_cat: Dict[str, float] = {k: 0.0 for k in CATEGORIES}
    for e in events:
        cat = LABEL_TO_CAT.get(e["label"])
        if cat:
            by_cat[cat] += _dur(e)
    observed = sum(by_cat.values())
    active = sum(by_cat[k] for k in ACTIVE_CATS)

    groups = _groups(events)
    rests = [g for g in groups if g["cat"] == "resting"]
    longest_rest = max(rests, key=lambda g: g["seconds"]) if rests else None

    barks = [e for e in events if e["label"] == "barking"]
    whines = [e for e in events if e["label"] == "whining"]
    presses = [e for e in events if e["label"] == "button_press"]
    imu = [e for e in events if e["source"] == "sensor" and e["label"] != "button_press"]

    # --- day timeline: 15-min buckets from the first hour seen (or 06:00) to now
    bucket_min = 15
    first = _ts(events[0]["started_at"]) if events else now
    if since is not None:
        since = since.astimezone()
        day_start = since.replace(minute=since.minute - since.minute % 15, second=0, microsecond=0)
    else:
        day_start = min(first, now).replace(minute=0, second=0, microsecond=0)
    n_buckets = max(1, int((now - day_start).total_seconds() // (bucket_min * 60)) + 1)
    fill = [dict.fromkeys(CATEGORIES, 0.0) for _ in range(n_buckets)]
    for e in events:
        cat = LABEL_TO_CAT.get(e["label"])
        if not cat:
            continue
        s, en = _ts(e["started_at"]), _ts(e["ended_at"])
        t = s
        while t < en:
            idx = int((t - day_start).total_seconds() // (bucket_min * 60))
            b_end = day_start + timedelta(minutes=bucket_min * (idx + 1))
            step_end = min(en, b_end)
            if 0 <= idx < n_buckets:
                fill[idx][cat] += (step_end - t).total_seconds()
            t = step_end
    sounds = [0] * n_buckets
    for e in barks + whines:
        idx = int((_ts(e["started_at"]) - day_start).total_seconds() // (bucket_min * 60))
        if 0 <= idx < n_buckets:
            sounds[idx] += 1
    buckets = []
    for i, f in enumerate(fill):
        top = max(f, key=f.get)
        buckets.append({"cat": top if f[top] > 0 else None, "sounds": sounds[i]})

    # --- moments worth a photo (newest first)
    moments = []
    for g in groups:
        c = CATEGORIES[g["cat"]]
        secs = g["seconds"]
        # thresholds sized so a 10-15 min live session still produces moments
        if g["cat"] == "resting" and secs >= 20 * 60:
            caption = f"Napped for {_fmt(secs)}"
        elif g["cat"] == "resting" and secs >= 90:
            caption = f"Lay down for {_fmt(secs)}"
        elif g["cat"] == "running" and secs >= 10:
            caption = f"Zoomies! Played for {_fmt(secs)}"
        elif g["cat"] == "walking" and secs >= 45:
            caption = f"Walked around for {_fmt(secs)}"
        elif g["cat"] == "exploring" and secs >= 20:
            caption = f"Sniffed around for {_fmt(secs)}"
        elif g["cat"] == "calm" and secs >= 90:
            caption = f"Sat or stood calmly for {_fmt(secs)}"
        else:
            continue
        moments.append({"time": g["start"].isoformat(), "caption": caption, "emoji": c["emoji"],
                        "event_id": g["first_event"], "thumb": has_thumb(g["first_event"])})
    for e in presses:
        moments.append({"time": _ts(e["started_at"]).isoformat(), "caption": "Asked to go out (pressed the button)",
                        "emoji": "🔘", "event_id": e["event_id"], "thumb": has_thumb(e["event_id"])})
    moments.sort(key=lambda m: m["time"], reverse=True)

    # --- recent behaviour changes (newest first, max 5)
    changes = [{"cat": g["cat"], "name": CATEGORIES[g["cat"]]["name"], "emoji": CATEGORIES[g["cat"]]["emoji"],
                "start": g["start"].isoformat(), "end": g["end"].isoformat(), "seconds": round(g["seconds"]),
                "event_id": g["first_event"], "thumb": has_thumb(g["first_event"])}
               for g in groups[-5:]][::-1]

    today_alerts = sorted(alerts, key=lambda a: a["triggered_at"], reverse=True)
    most_active_hour = None
    if active > 0:
        per_hour: Dict[int, float] = {}
        for i, f in enumerate(fill):
            hour = (day_start + timedelta(minutes=bucket_min * i)).hour
            per_hour[hour] = per_hour.get(hour, 0.0) + sum(f[k] for k in ACTIVE_CATS)
        most_active_hour = max(per_hour, key=per_hour.get)

    return {
        "date": now.strftime("%A, %d %B"),
        "generated_at": now.isoformat(),
        "since": (since or day_start).isoformat(),
        "profile": profile,
        "observed_s": round(observed),
        "categories": [{"key": k, "name": c["name"], "emoji": c["emoji"], "color": c["color"],
                        "seconds": round(by_cat[k])} for k, c in CATEGORIES.items()],
        "active_s": round(active),
        "activity_goal_s": goal_min * 60,
        "rest_s": round(by_cat["resting"]),
        "longest_rest": ({"start": longest_rest["start"].isoformat(), "seconds": round(longest_rest["seconds"])}
                         if longest_rest else None),
        "most_active_hour": most_active_hour,
        "barks": len(barks),
        "whines": len(whines),
        "button_presses": len(presses),
        # demo-mode sample story (services/summary/demo_story.py) is tagged demo_seed
        "includes_demo_data": any(e.get("evidence", {}).get("detector") == "demo_seed" for e in events),
        # real analysis of recorded clips placed at staged times (scripts/analyze_clips.py)
        "from_recorded_clips": any("clip_id" in e.get("evidence", {}) for e in events),
        "last_event_at": _ts(events[-1]["ended_at"]).isoformat() if events else None,
        "collar": {"connected": bool(imu), "events": len(imu)},
        "timeline": {"start": day_start.isoformat(), "bucket_min": bucket_min, "buckets": buckets},
        "moments": moments[:6],
        # the whole day as behaviour changes, for questions like "since I left?"
        "log": [{"cat": g["cat"], "start": g["start"].isoformat(), "seconds": round(g["seconds"])} for g in groups],
        "changes": changes,
        "alerts": [{"time": _ts(a["triggered_at"]).isoformat(), "severity": a["severity"], "rule_id": a["rule_id"],
                    "message": a["message"], "status": a["status"]} for a in today_alerts[:10]],
        "sounds": [{"time": _ts(e["started_at"]).isoformat(), "label": e["label"]}
                   for e in sorted(barks + whines, key=lambda e: e["started_at"], reverse=True)[:10]],
    }
