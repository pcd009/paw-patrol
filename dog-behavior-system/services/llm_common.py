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

def _load_dotenv() -> None:
    """Read KEY=VALUE lines from the project-root .env (gitignored) without overriding
    variables already exported in the shell. Lets every process pick up the API key."""
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


_load_dotenv()
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")

_client = None
_client_checked = False
_error_count = 0


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[claude] ANTHROPIC_API_KEY not set -- Claude calls use the mock fallback")
        _client = None
        return None
    # Keys that aren't scoped to a workspace (e.g. some org/event-issued keys) must name the
    # workspace on every request, otherwise the API returns 400 invalid_request_error.
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    try:
        import anthropic
        _client = anthropic.Anthropic(default_headers=headers)
    except Exception:
        _client = None
    return _client


_UNSUPPORTED_SCHEMA_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                            "multipleOf", "minLength", "maxLength"}


def _api_schema(schema: Any) -> Any:
    """Copy of `schema` without keywords structured outputs rejects (numeric/length bounds).
    Callers clamp/validate those bounds in code after parsing."""
    if isinstance(schema, dict):
        return {k: _api_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [_api_schema(v) for v in schema]
    return schema


def clamp01(x: Any, default: float = 0.5) -> float:
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return default


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
    output_config: Dict[str, Any] = {"format": {"type": "json_schema", "schema": _api_schema(schema)}}
    if not CLAUDE_MODEL.startswith("claude-haiku"):  # Haiku 4.5 rejects the effort parameter
        output_config["effort"] = effort
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config=output_config,
        )
        if resp.stop_reason == "refusal":
            return None, "mock"
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            return None, "mock"
        return json.loads(text), CLAUDE_MODEL
    except Exception as e:
        # Covers anthropic.APIError, anthropic.APIConnectionError, json errors, and any
        # SDK surprises -- a hackathon demo must never crash because of a flaky network call.
        # But say so, so a bad key/model doesn't silently look like "mock mode".
        global _error_count
        _error_count += 1
        if _error_count <= 3 or _error_count % 50 == 0:
            print(f"[claude] call failed ({type(e).__name__}): {str(e)[:200]} -- using fallback")
        return None, "mock"


def is_live() -> bool:
    return _get_client() is not None
