"""Validate objects against the frozen contracts.

    python3 -m contracts.validate            # check every fixture
    from contracts.validate import validate  # validate(obj, "behavior_event")

Uses `jsonschema` if installed (pip install jsonschema); otherwise falls back to
checking required keys only, so nobody is blocked on dependencies.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "fixtures"

_SCHEMAS = {p.stem: json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.json")}

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    _REGISTRY = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in _SCHEMAS.values()
    )
except ImportError:  # minimal fallback
    Draft202012Validator = None


def validate(obj: dict, schema_name: str) -> None:
    """Raise ValueError if obj does not match contracts/schemas/<schema_name>.json."""
    schema = _SCHEMAS[schema_name]
    if Draft202012Validator is not None:
        errors = sorted(
            Draft202012Validator(schema, registry=_REGISTRY).iter_errors(obj), key=lambda e: list(e.path)
        )
        if errors:
            msgs = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5])
            raise ValueError(f"{schema_name}: {msgs}")
        return
    missing = [k for k in schema.get("required", []) if k not in obj]
    if missing:
        raise ValueError(f"{schema_name}: missing keys {missing}")


FIXTURES = {
    "demo-events.json": ("behavior_event", True),
    "demo-alerts.json": ("alert", True),
    "demo-context.json": ("context_packet", False),
    "demo-triage.json": ("triage_result", False),
    "demo-state.json": ("demo_state", False),
}


def main() -> int:
    if Draft202012Validator is None:
        print("note: jsonschema not installed, only checking required keys")
    ok = True
    for fname, (schema_name, is_list) in FIXTURES.items():
        path = FIXTURE_DIR / fname
        if not path.exists():
            print(f"SKIP {fname} (missing)")
            continue
        data = json.loads(path.read_text())
        try:
            for item in data if is_list else [data]:
                validate(item, schema_name)
            print(f"OK   {fname}")
        except ValueError as e:
            ok = False
            print(f"FAIL {fname}: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
