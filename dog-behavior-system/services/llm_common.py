"""Single choke point for every Claude call in the system.

Every caller gets back (data: dict | None, model_label: str). model_label is either
CLAUDE_MODEL (a real call succeeded) or "mock" (no key, refusal, or any API error) --
the dashboard shows this badge so nobody mistakes a mocked demo for a live one.

The LLM only ever informs vision_labeler.py (a behaviour label) and triage/ask (an
explanation). It never creates or edits an Alert -- see services/context_rules/engine.py.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

_client = None
_client_checked = False


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _client = None
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic()
    except Exception:
        _client = None
    return _client


def call_claude_json(
    system: str,
    user_content: List[Dict[str, Any]],
    schema: Dict[str, Any],
    max_tokens: int = 1024,
    effort: str = "low",
) -> Tuple[Optional[dict], str]:
    """Returns (parsed_json_or_None, model_label). Never raises."""
    client = _get_client()
    if client is None:
        return None, "mock"
    try:
        import anthropic
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            return None, "mock"
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            return None, "mock"
        return json.loads(text), CLAUDE_MODEL
    except Exception:
        # Covers anthropic.APIError, anthropic.APIConnectionError, json errors, and any
        # SDK surprises -- a hackathon demo must never crash because of a flaky network call.
        return None, "mock"


def is_live() -> bool:
    return _get_client() is not None
