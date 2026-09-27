"""Owner chat box: answer(question, recent_packets, recent_triage) using only the context the
dashboard already shows -- never invents new events, and says plainly when it's mocked."""
from __future__ import annotations

import json
from typing import List

from services.llm_common import call_claude_json

SYSTEM_PROMPT = """You are answering a dog owner's question about their Labrador's recent \
monitored behaviour, using ONLY the recent context packets and triage results provided below. \
Do not claim to know the dog's internal state for certain -- hedge appropriately. If the \
provided history doesn't contain enough information to answer, say so plainly instead of \
guessing or inventing events. Keep answers to 2-4 sentences. Respond with only the required \
JSON, no extra commentary."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "based_on_context"],
    "properties": {
        "answer": {"type": "string"},
        "based_on_context": {
            "type": "boolean",
            "description": "true if the answer used the provided history, false if it's a generic/no-data reply",
        },
    },
}


def _mock_answer(question: str, packets: List[dict], triage_results: List[dict]) -> dict:
    if not packets and not triage_results:
        return {
            "answer": "I don't have any recent monitoring data yet to answer that from "
                      "(mock mode: no ANTHROPIC_API_KEY set).",
            "based_on_context": False,
        }
    if triage_results:
        last = triage_results[-1]
        return {
            "answer": f"(mock) Latest assessment: {last['decision']} -- {last['owner_message']}",
            "based_on_context": True,
        }
    return {"answer": "(mock) No triage results yet, but events are being recorded.", "based_on_context": False}


def answer(question: str, packets: List[dict], triage_results: List[dict]) -> dict:
    context_text = json.dumps(
        {"recent_context_packets": packets[-3:], "recent_triage_results": triage_results[-3:]}, indent=2
    )
    user_text = f"Owner question: {question}\n\nRecent history (JSON):\n{context_text}"
    data, model_label = call_claude_json(
        SYSTEM_PROMPT, [{"type": "text", "text": user_text}], SCHEMA, max_tokens=400
    )
    if data is None:
        data = _mock_answer(question, packets, triage_results)
        model_label = "mock"
    return {
        "answer": data.get("answer", ""),
        "based_on_context": bool(data.get("based_on_context", False)),
        "model": model_label,
    }
