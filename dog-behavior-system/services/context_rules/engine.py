"""Person 2: deterministic rules + context packets.

    evaluate_rules(events, existing_alerts) -> list[Alert]   (new alerts only)
    build_context(events, alerts, window_end, ...) -> ContextPacket

Pure functions over the contracts: no I/O, no LLM. Thresholds live in RULES.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from contracts.common import SCHEMA_VERSION, new_id, parse_ts, to_iso

VOCAL = {"barking", "whining"}

# Tune these at the venue with the real dog. Only rules may emit "critical".
RULES = {
    "repeated_barking_window": {
        "severity": "warning", "window_s": 60, "labels": {"barking"}, "min_count": 3,
        "reason_code": "three_bark_events_within_60_seconds",
        "message": "Repeated barking: 3+ bark events within 60 seconds.",
    },
    "sustained_whining": {
        "severity": "warning", "window_s": 60, "labels": {"whining"}, "min_duration_ms": 15000,
        "reason_code": "whining_over_15s_within_60_seconds",
        "message": "Sustained whining: over 15 seconds within one minute.",
    },
    "prolonged_distress": {
        "severity": "critical", "window_s": 120, "labels": VOCAL, "min_count": 6,
        "reason_code": "six_vocal_events_within_120_seconds",
        "message": "Prolonged vocal distress: 6+ bark/whine events within 2 minutes.",
    },
    "dog_button_request": {
        "severity": "info", "window_s": 30, "labels": {"button_press"}, "min_count": 1,
        "reason_code": "dog_pressed_request_button",
        "message": "Your dog pressed the door button.",
    },
}


def _duration_ms(e: dict) -> int:
    return int((parse_ts(e["ended_at"]) - parse_ts(e["started_at"])).total_seconds() * 1000)


def evaluate_rules(events: List[dict], existing_alerts: Optional[List[dict]] = None) -> List[dict]:
    """Return NEW alerts. A rule will not re-fire until its window has passed since its last trigger."""
    existing_alerts = existing_alerts or []
    last_fired: Dict[str, datetime] = {}
    for a in existing_alerts:
        t = parse_ts(a["triggered_at"])
        if a["rule_id"] not in last_fired or t > last_fired[a["rule_id"]]:
            last_fired[a["rule_id"]] = t

    events = sorted(events, key=lambda e: e["started_at"])
    new_alerts: List[dict] = []
    for rule_id, rule in RULES.items():
        window = timedelta(seconds=rule["window_s"])
        for i, anchor in enumerate(events):
            if anchor["label"] not in rule["labels"]:
                continue
            now = parse_ts(anchor["ended_at"])
            if rule_id in last_fired and now - last_fired[rule_id] < window:
                continue
            hits = [e for e in events[: i + 1]
                    if e["label"] in rule["labels"] and now - parse_ts(e["started_at"]) <= window]
            fired = (
                len(hits) >= rule["min_count"] if "min_count" in rule
                else sum(_duration_ms(e) for e in hits) >= rule["min_duration_ms"]
            )
            if fired:
                last_fired[rule_id] = now
                new_alerts.append({
                    "schema_version": SCHEMA_VERSION,
                    "alert_id": new_id("alert"),
                    "subject_id": anchor["subject_id"],
                    "rule_id": rule_id,
                    "severity": rule["severity"],
                    "status": "active",
                    "triggered_at": to_iso(now),
                    "evidence_event_ids": [e["event_id"] for e in hits],
                    "reason_code": rule["reason_code"],
                    "message": rule["message"],
                })
    return new_alerts


def resolve_stale(alerts: List[dict], now: datetime, ttl_s: int = 180) -> None:
    """Mark alerts resolved once they are older than ttl_s (in place)."""
    for a in alerts:
        if a["status"] == "active" and (now - parse_ts(a["triggered_at"])).total_seconds() > ttl_s:
            a["status"] = "resolved"


def build_context(
    events: List[dict],
    alerts: List[dict],
    window_end: datetime,
    window_s: int = 60,
    subject_id: str = "demo_dog_01",
    history_notes: Optional[List[str]] = None,
    similar_pattern_seen_today: bool = False,
) -> dict:
    window_start = window_end - timedelta(seconds=window_s)
    in_window = sorted(
        (e for e in events
         if parse_ts(e["ended_at"]) >= window_start and parse_ts(e["started_at"]) <= window_end),
        key=lambda e: e["started_at"],
    )
    labels = Counter(e["label"] for e in in_window)
    durations: Dict[str, int] = {}
    for e in in_window:
        durations[e["label"]] = durations.get(e["label"], 0) + _duration_ms(e)

    # A "visit" = entering a zone (consecutive video events in the same zone count once)
    zone_visits: Dict[str, int] = {}
    prev_zone = None
    for e in in_window:
        if e["source"] != "video" or e["zone"] == "unknown":
            continue
        if e["zone"] != prev_zone:
            zone_visits[e["zone"]] = zone_visits.get(e["zone"], 0) + 1
            prev_zone = e["zone"]

    return {
        "schema_version": SCHEMA_VERSION,
        "context_id": new_id("ctx"),
        "subject_id": subject_id,
        "created_at": to_iso(window_end),
        "window_start": to_iso(window_start),
        "window_end": to_iso(window_end),
        "events": in_window,
        "active_alerts": [a for a in alerts if a["status"] == "active"],
        "derived_metrics": {
            "bark_count": labels["barking"],
            "whine_count": labels["whining"],
            "button_press_count": labels["button_press"],
            "label_duration_ms": durations,
            "zone_visits": zone_visits,
            "last_zone": prev_zone or "unknown",
        },
        "recent_history": {
            "similar_pattern_seen_today": similar_pattern_seen_today,
            "notes": history_notes or [],
        },
    }
