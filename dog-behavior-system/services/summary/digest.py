"""Claude-written "today with <dog>" story for an owner checking in from work.

Built from the daily summary (services/summary/daily.py), not raw events, so it scales to a
full day. Advisory only: never creates or changes alerts. Mock fallback without an API key.
"""
from __future__ import annotations

import json
from datetime import datetime

from services.llm_common import call_claude_json

SYSTEM_PROMPT = """You write a short daily check-in about a pet dog for its owner, who is at \
work and glancing at an app. You get a JSON summary of what the home camera, microphone and \
collar observed today. Write like a thoughtful dog-sitter texting an update: warm, concrete, brief.

Rules:
- Cover only the period from monitoring_since to now; if it is short, say so naturally \
(e.g. "In the last 10 minutes...").
- Use only facts in the summary (times are local). Do not invent events, places, or causes.
- You describe observed behaviour, not the dog's mind: hedge interpretations ("seems settled", \
"may have been reacting to something outside"), never state feelings as fact.
- A normal, quiet day is good news: say so plainly; don't manufacture concern.
- Mention alerts only as reported by the summary; you cannot create, escalate or dismiss them.
- Consider the breed and the activity goal when judging whether activity looks low or high.
- No ids, no JSON field names, no technical words (events, detections, confidence).

Fields:
- headline: at most 8 words capturing the day so far, e.g. "A calm, sleepy day for Bruno".
- parts: one entry per part of the day that has observations, in time order, using the \
day_log's "part" values (Early morning, Morning, Afternoon, Evening, Night). Each text is ONE \
short sentence (max ~18 words) with the notable things and their times, e.g. "Whined briefly \
when you left at 9:02, then settled into a 2-hour nap." Skip parts with nothing observed.
- check: one short sentence for the owner: what they might do, or "Nothing needs your attention."
- check_level: "ok" if nothing needs attention, "look" if the owner should check something."""

PARTS = ["Early morning", "Morning", "Afternoon", "Evening", "Night"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "parts", "check", "check_level"],
    "properties": {
        "headline": {"type": "string"},
        "parts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["part", "text"],
            "properties": {"part": {"enum": PARTS}, "text": {"type": "string"}}}},
        "check": {"type": "string"},
        "check_level": {"enum": ["ok", "look"]},
    },
}


def part_of_day(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 8:
        return "Early morning"
    if 8 <= h < 12:
        return "Morning"
    if 12 <= h < 17:
        return "Afternoon"
    if 17 <= h < 21:
        return "Evening"
    return "Night"


def _compact(summary: dict) -> dict:
    """Just the facts Claude needs, with human-readable local times."""
    def hm(iso):
        return datetime.fromisoformat(iso).strftime("%H:%M") if iso else None
    return {
        "dog": summary.get("profile", {}),
        "date": summary["date"],
        "now": hm(summary["generated_at"]),
        "monitoring_since": hm(summary.get("since")),
        "minutes_observed": round(summary["observed_s"] / 60),
        "minutes_by_activity": {c["name"]: round(c["seconds"] / 60) for c in summary["categories"]},
        "active_minutes": round(summary["active_s"] / 60),
        "activity_goal_minutes": round(summary["activity_goal_s"] / 60),
        "longest_nap": ({"started": hm(summary["longest_rest"]["start"]),
                         "minutes": round(summary["longest_rest"]["seconds"] / 60)}
                        if summary["longest_rest"] else None),
        "most_active_hour": summary["most_active_hour"],
        "barks": summary["barks"], "whines": summary["whines"],
        "door_button_presses": summary["button_presses"],
        "recent_changes": [{"activity": c["name"], "from": hm(c["start"]), "minutes": round(c["seconds"] / 60)}
                           for c in summary["changes"]],
        "alerts_today": [{"time": hm(a["time"]), "severity": a["severity"], "what": a["message"]}
                         for a in summary["alerts"]],
        "sounds_today": [{"time": hm(s["time"]), "sound": s["label"]} for s in summary["sounds"]],
        "collar_connected": summary["collar"]["connected"],
        "day_log": [{"part": part_of_day(datetime.fromisoformat(g["start"])),
                     "from": hm(g["start"]), "activity": g["cat"], "minutes": round(g["seconds"] / 60, 1)}
                    for g in summary.get("log", [])],
    }


_VERB = {"resting": "rested", "calm": "was calm and awake", "walking": "walked around",
         "running": "ran and played", "exploring": "sniffed around"}


def _mock(summary: dict) -> dict:
    """Template version of the same structure, used without an API key."""
    name = summary.get("profile", {}).get("name", "Your dog")
    by_part: dict = {}
    for g in summary.get("log", []):
        by_part.setdefault(part_of_day(datetime.fromisoformat(g["start"])), []).append(g)
    sounds_by_part: dict = {}
    for x in summary.get("sounds", []):
        sounds_by_part.setdefault(part_of_day(datetime.fromisoformat(x["time"])), []).append(x)
    parts = []
    for part in PARTS:
        groups = by_part.get(part, [])
        if not groups:
            continue
        main = max(groups, key=lambda g: g["seconds"])
        mins = round(main["seconds"] / 60)
        text = f"Mostly {_VERB[main['cat']]} ({mins} min from {datetime.fromisoformat(main['start']):%H:%M})"
        snd = sounds_by_part.get(part, [])
        if snd:
            text += f", {len(snd)} bark/whine sound{'s' if len(snd) > 1 else ''}"
        parts.append({"part": part, "text": text + "."})
    if not parts:
        return {"headline": f"Waiting to see {name}", "parts": [],
                "check": "Nothing needs your attention.", "check_level": "ok"}
    top = max(summary["categories"], key=lambda c: c["seconds"])
    look = any(a["status"] == "active" for a in summary["alerts"])
    return {"headline": f"A mostly {top['name'].lower()} day for {name}", "parts": parts,
            "check": "Take a look at the alert above." if look else "Nothing needs your attention.",
            "check_level": "look" if look else "ok"}


def daily_digest(summary: dict) -> dict:
    facts = _compact(summary)
    data, model_label = call_claude_json(
        SYSTEM_PROMPT,
        [{"type": "text", "text": "Today's summary:\n" + json.dumps(facts, indent=1)}],
        SCHEMA, max_tokens=700,
    )
    out = data if data else _mock(summary)
    out["parts"] = [p for p in out.get("parts", []) if p.get("text")][:5]
    out["model"] = model_label if data else "mock"
    out["generated_at"] = summary["generated_at"]
    return out
