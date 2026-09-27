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
- Use only facts in the summary (times are local). Do not invent events, places, or causes.
- You describe observed behaviour, not the dog's mind: hedge interpretations ("seems settled", \
"may have been reacting to something outside"), never state feelings as fact.
- A normal, quiet day is good news: say so plainly; don't manufacture concern.
- Mention alerts only as reported by the summary; you cannot create, escalate or dismiss them.
- Consider the breed and the activity goal when judging whether activity looks low or high.
- No ids, no JSON field names, no technical words (events, detections, confidence).

Fields:
- headline: at most 8 words, e.g. "A calm, sleepy morning for Bruno".
- story: 2-3 sentences covering the day so far in time order.
- highlights: 1-3 very short items worth knowing (e.g. "Longest nap: 1h 40m after you left").
- check: one short sentence: what the owner might do, or "Nothing needs your attention." """

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "story", "highlights", "check"],
    "properties": {
        "headline": {"type": "string"},
        "story": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "check": {"type": "string"},
    },
}


def _compact(summary: dict) -> dict:
    """Just the facts Claude needs, with human-readable local times."""
    def hm(iso):
        return datetime.fromisoformat(iso).strftime("%H:%M") if iso else None
    return {
        "dog": summary.get("profile", {}),
        "date": summary["date"],
        "now": hm(summary["generated_at"]),
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
    }


def _mock(summary: dict) -> dict:
    name = summary.get("profile", {}).get("name", "Your dog")
    if summary["observed_s"] < 60:
        return {"headline": f"Waiting to see {name}", "story": f"{name} hasn't been seen much yet today.",
                "highlights": [], "check": "Nothing needs your attention."}
    top = max(summary["categories"], key=lambda c: c["seconds"])
    highlights = []
    if summary["longest_rest"]:
        highlights.append(f"Longest nap: {round(summary['longest_rest']['seconds'] / 60)} min")
    highlights.append(f"Active {round(summary['active_s'] / 60)} of {round(summary['activity_goal_s'] / 60)} min goal")
    if summary["barks"] or summary["whines"]:
        highlights.append(f"{summary['barks']} barks, {summary['whines']} whines")
    return {
        "headline": f"Mostly {top['name'].lower()} today",
        "story": (f"So far {name} has spent most of the observed time {top['name'].lower()}. "
                  f"(Mock summary: set an API key for a Claude-written update.)"),
        "highlights": highlights[:3],
        "check": "Check the alerts panel." if summary["alerts"] else "Nothing needs your attention.",
    }


def daily_digest(summary: dict) -> dict:
    facts = _compact(summary)
    data, model_label = call_claude_json(
        SYSTEM_PROMPT,
        [{"type": "text", "text": "Today's summary:\n" + json.dumps(facts, indent=1)}],
        SCHEMA, max_tokens=600,
    )
    out = data if data else _mock(summary)
    out["highlights"] = [h for h in out.get("highlights", []) if h][:3]
    out["model"] = model_label if data else "mock"
    out["generated_at"] = summary["generated_at"]
    return out
