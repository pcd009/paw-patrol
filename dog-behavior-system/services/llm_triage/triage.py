"""LLM interpretation of one ContextPacket -> TriageResult. Advisory only: this module can never
create, modify, resolve, or dismiss an Alert -- that's exclusively services/context_rules/engine.py.

triage() validates in code that any event/alert ids the model cites are actually a subset of the
packet it was given; if the model cites something outside the packet, we downgrade its decision
to insufficient_evidence rather than trust a possibly-hallucinated citation.
"""
from __future__ import annotations

import json

from contracts.common import SCHEMA_VERSION, new_id, now_iso
from services.llm_common import call_claude_json

DECISIONS = ["routine", "monitor", "notify", "insufficient_evidence"]

SYSTEM_PROMPT = """You are an assistant that explains a pet Labrador retriever's recent behaviour \
to its owner, using ONLY the structured events and alerts in the context packet you are given.

Rules you must follow:
- You are interpreting sensor-derived events, not the dog's mind: never claim to know what the \
dog is "thinking" or "feeling" for certain. Describe what was observed and offer plausible, \
hedged explanations ("this can indicate ...", not "your dog is ...").
- Cite the event_ids and/or alert_ids that support your assessment in evidence_event_ids / \
referenced_alert_ids. Only cite ids that are literally present in the context packet.
- You cannot create, escalate, dismiss, or modify any alert. Alerts come from a separate \
deterministic rule engine; you only explain them.
- Be Labrador-aware (food-motivated, often vocalizes near mealtimes or the door, generally a \
sturdy/even-tempered breed) but stay cautious: state uncertainty explicitly in the \
`uncertainty` field, and if the evidence is thin or ambiguous prefer monitor or \
insufficient_evidence over notify.
- decision: routine (nothing notable), monitor (worth watching, not urgent), notify (owner \
should be told/check now), insufficient_evidence (not enough signal to say anything useful).
- owner_message: 1-3 short, plain-language sentences suitable for a phone notification.
Respond with only the required JSON, no extra commentary."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision", "confidence", "evidence_event_ids", "referenced_alert_ids",
        "owner_message", "uncertainty", "suggested_check",
    ],
    "properties": {
        "decision": {"enum": DECISIONS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_event_ids": {"type": "array", "items": {"type": "string"}},
        "referenced_alert_ids": {"type": "array", "items": {"type": "string"}},
        "owner_message": {"type": "string"},
        "uncertainty": {"type": "string"},
        "suggested_check": {"type": "string"},
    },
}


def _mock_triage(packet: dict) -> dict:
    """Deterministic, dependency-free stand-in for Claude so the demo runs with no API key."""
    alerts = packet["active_alerts"]
    events = packet["events"]
    recent_ids = [e["event_id"] for e in events[-5:]]
    alert_ids = [a["alert_id"] for a in alerts]
    if any(a["severity"] == "critical" for a in alerts):
        decision = "notify"
        msg = "Repeated barking/whining over the last couple of minutes -- please check on your dog."
    elif alerts:
        decision = "monitor"
        msg = "Some barking, whining, or a button press was noticed recently -- may be worth a look."
    elif packet["derived_metrics"]["bark_count"] or packet["derived_metrics"]["whine_count"]:
        decision = "monitor"
        msg = "A little vocalizing recently, nothing sustained yet."
    else:
        decision = "routine"
        msg = "Nothing notable in the last minute."
    return {
        "decision": decision,
        "confidence": 0.5,
        "evidence_event_ids": recent_ids,
        "referenced_alert_ids": alert_ids,
        "owner_message": msg,
        "uncertainty": "Mock triage (no ANTHROPIC_API_KEY set): a fixed rule-of-thumb summary of "
                       "the active alerts and recent events, not a model read of the context.",
        "suggested_check": "Look in on your dog if you're nearby.",
    }


def triage(packet: dict) -> dict:
    valid_event_ids = {e["event_id"] for e in packet["events"]}
    valid_alert_ids = {a["alert_id"] for a in packet["active_alerts"]}

    user_text = (
        "Context packet (JSON) for one time window. Only cite ids present in it.\n\n"
        + json.dumps(packet, indent=2)
    )
    data, model_label = call_claude_json(
        SYSTEM_PROMPT, [{"type": "text", "text": user_text}], SCHEMA, max_tokens=700
    )

    if data is None:
        data = _mock_triage(packet)
        model_label = "mock"

    raw_evidence = data.get("evidence_event_ids", []) or []
    raw_alerts = data.get("referenced_alert_ids", []) or []
    evidence_ids = [i for i in raw_evidence if i in valid_event_ids]
    alert_ids = [i for i in raw_alerts if i in valid_alert_ids]
    decision = data.get("decision", "insufficient_evidence")
    if decision not in DECISIONS:
        decision = "insufficient_evidence"
    hallucinated = len(evidence_ids) != len(raw_evidence) or len(alert_ids) != len(raw_alerts)
    if hallucinated and decision != "insufficient_evidence":
        decision = "insufficient_evidence"

    return {
        "schema_version": SCHEMA_VERSION,
        "triage_id": new_id("triage"),
        "context_id": packet["context_id"],
        "subject_id": packet["subject_id"],
        "created_at": now_iso(),
        "model": model_label,
        "decision": decision,
        "confidence": float(data.get("confidence", 0.4) or 0.4),
        "evidence_event_ids": evidence_ids,
        "referenced_alert_ids": alert_ids,
        "owner_message": data.get("owner_message") or "No assessment available.",
        "uncertainty": data.get("uncertainty") or "Uncertainty not provided.",
        "suggested_check": data.get("suggested_check") or "",
    }
