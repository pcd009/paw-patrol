"""FastAPI orchestrator: the single source of truth (DemoState) for the dashboard and the
hardware. Runs the deterministic rule engine on every new event, runs LLM triage at most every
~15s (only when there's something new to say), and derives device state (led/buzzer) ONLY from
active alerts -- never from the LLM.

    python -m app.orchestrator.server               # http://localhost:8000
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from app.config import get as cfg_get
from app.io.outputs import build_sinks
from contracts.common import SCHEMA_VERSION, new_id, now_iso, parse_ts, to_iso
from contracts.validate import validate
from services.context_rules.engine import build_context, evaluate_rules, resolve_stale
from services.llm_triage.ask import answer as llm_answer
from services.llm_triage.triage import triage as llm_triage

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DIR = ROOT / "app" / "dashboard"
FIXTURE_PATH = ROOT / "contracts" / "fixtures" / "demo-events.json"

TRIAGE_MIN_INTERVAL_S = cfg_get("triage", "min_interval_s", default=15)
TRIAGE_WINDOW_S = cfg_get("triage", "window_s", default=60)

app = FastAPI(title="Dog Behaviour Orchestrator")


class Store:
    def __init__(self):
        self.subject_id = cfg_get("subject_id", default="demo_dog_01")
        self.mode = "live"
        self.events: list = []
        self.alerts: list = []
        self.context_packets: list = []
        self.triage_results: list = []
        self.last_triage_at: Optional[datetime] = None
        self.last_triage_event_count = 0
        self.last_triage_alert_ids: set = set()
        self.triage_running = False
        self.last_zone = "unknown"
        self.latest_frame: Optional[bytes] = None
        self.replay_running = False
        self.lock = threading.RLock()

        sinks_cfg = cfg_get("adapters", "output_sinks", default=["console", "device_state"])
        self.sinks, self.device_sink = build_sinks(
            sinks_cfg,
            serial_port=cfg_get("adapters", "serial_port"),
            webhook_url=cfg_get("adapters", "webhook_url"),
        )


store = Store()


def compute_device_state(alerts: list) -> tuple:
    active = [a for a in alerts if a["status"] == "active"]
    if any(a["severity"] == "critical" for a in active):
        return "red", True
    if any(a["severity"] == "warning" for a in active):
        return "amber", False
    return "green", False


def _push_device_state() -> None:
    led, buzzer = compute_device_state(store.alerts)
    for sink in store.sinks:
        sink.update_device(led, buzzer)


def maybe_run_triage() -> None:
    now = datetime.now(timezone.utc)
    with store.lock:
        active_alert_ids = {a["alert_id"] for a in store.alerts if a["status"] == "active"}
        alerts_changed = active_alert_ids != store.last_triage_alert_ids
        has_new_events = len(store.events) != store.last_triage_event_count
        interval_ok = (
            store.last_triage_at is None
            or (now - store.last_triage_at).total_seconds() >= TRIAGE_MIN_INTERVAL_S
        )
        if store.triage_running or not interval_ok or not (has_new_events or alerts_changed):
            return
        store.triage_running = True
        store.last_triage_at = now
        store.last_triage_event_count = len(store.events)
        store.last_triage_alert_ids = active_alert_ids
        events_snapshot = list(store.events)
        alerts_snapshot = list(store.alerts)
        subject_id = store.subject_id

    def worker():
        try:
            packet = build_context(
                events_snapshot, alerts_snapshot, now, window_s=TRIAGE_WINDOW_S, subject_id=subject_id
            )
            validate(packet, "context_packet")
            result = llm_triage(packet)
            validate(result, "triage_result")
            with store.lock:
                store.context_packets.append(packet)
                store.triage_results.append(result)
            for sink in store.sinks:
                sink.notify("triage", result)
        except Exception as e:
            print(f"[triage] failed: {e}")
        finally:
            with store.lock:
                store.triage_running = False

    threading.Thread(target=worker, daemon=True).start()


def ingest_event(event: dict) -> None:
    validate(event, "behavior_event")
    with store.lock:
        store.events.append(event)
        if event["source"] == "video" and event["zone"] != "unknown":
            store.last_zone = event["zone"]
        new_alerts = evaluate_rules(store.events, store.alerts)
        store.alerts.extend(new_alerts)
        resolve_stale(store.alerts, datetime.now(timezone.utc))
        _push_device_state()
        for sink in store.sinks:
            sink.notify("event", event)
            for a in new_alerts:
                sink.notify("alert", a)
    maybe_run_triage()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/events")
async def post_event(request: Request):
    event = await request.json()
    with store.lock:
        if not store.replay_running:
            store.mode = "live"
    try:
        ingest_event(event)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True}


@app.post("/api/sensor/button")
async def sensor_button():
    """The ESP32 dog-pressable button hits this with no body at all -- the orchestrator builds
    the whole BehaviorEvent, so the device needs no JSON library."""
    now = datetime.now(timezone.utc)
    with store.lock:
        zone = store.last_zone if store.last_zone != "unknown" else "door_area"
        subject_id = store.subject_id
        if not store.replay_running:
            store.mode = "live"
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id("evt"),
        "subject_id": subject_id,
        "source": "sensor",
        "label": "button_press",
        "confidence": 1.0,
        "started_at": to_iso(now),
        "ended_at": to_iso(now),
        "zone": zone,
        "evidence": {"detector": "esp32_button", "device_id": "esp32_button_01"},
    }
    ingest_event(event)
    return {"ok": True, "event_id": event["event_id"]}


@app.post("/api/frame")
async def post_frame(request: Request):
    body = await request.body()
    with store.lock:
        store.latest_frame = body
    return {"ok": True}


@app.get("/api/frame.jpg")
async def get_frame():
    with store.lock:
        frame = store.latest_frame
    if frame is None:
        frame = _placeholder_jpeg()
    return Response(content=frame, media_type="image/jpeg")


@app.get("/api/state")
async def get_state():
    with store.lock:
        state = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "subject_id": store.subject_id,
            "mode": store.mode,
            "events": store.events[-200:],
            "alerts": store.alerts[-100:],
            "context_packets": store.context_packets[-20:],
            "triage_results": store.triage_results[-20:],
            "device": {"led": store.device_sink.led, "buzzer": store.device_sink.buzzer},
        }
    return state


@app.get("/api/device")
async def get_device():
    """Plain text on purpose -- the ESP32 LED/buzzer mirror needs no JSON library."""
    with store.lock:
        text = store.device_sink.as_plain_text()
    return PlainTextResponse(text)


@app.post("/api/ask")
async def post_ask(request: Request):
    body = await request.json()
    question = body.get("question", "")
    with store.lock:
        packets = list(store.context_packets)
        triage_results = list(store.triage_results)
    result = llm_answer(question, packets, triage_results)
    return result


@app.post("/api/replay")
async def post_replay(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    speed = float(body.get("speed", 1.0)) if isinstance(body, dict) else 1.0
    with store.lock:
        if store.replay_running:
            return JSONResponse({"ok": False, "error": "replay already running"}, status_code=409)
        store.replay_running = True
    threading.Thread(target=_run_replay, args=(speed,), daemon=True).start()
    return {"ok": True, "speed": speed}


def _run_replay(speed: float) -> None:
    try:
        events = json.loads(FIXTURE_PATH.read_text())
        events = sorted(events, key=lambda e: e["started_at"])
        if not events:
            return
        t0_fixture = parse_ts(events[0]["started_at"])
        t0_wall = datetime.now(timezone.utc)

        with store.lock:
            store.mode = "replay"
            store.events.clear()
            store.alerts.clear()
            store.context_packets.clear()
            store.triage_results.clear()
            store.last_triage_at = None
            store.last_triage_event_count = 0
            store.last_triage_alert_ids = set()
            store.last_zone = "unknown"
        _push_device_state()

        prev_offset = 0.0
        speed = max(speed, 0.01)
        for e in events:
            offset_s = (parse_ts(e["started_at"]) - t0_fixture).total_seconds() / speed
            wait = offset_s - prev_offset
            if wait > 0:
                time.sleep(wait)
            prev_offset = offset_s
            dur = (parse_ts(e["ended_at"]) - parse_ts(e["started_at"])).total_seconds() / speed
            new_started = t0_wall + timedelta(seconds=offset_s)
            new_ended = new_started + timedelta(seconds=max(dur, 0))
            ev = dict(e)
            ev["started_at"] = to_iso(new_started)
            ev["ended_at"] = to_iso(new_ended)
            try:
                ingest_event(ev)
            except Exception as ex:
                print(f"[replay] skip bad event: {ex}")
        print("[replay] done")
    finally:
        with store.lock:
            store.replay_running = False
            store.mode = "live"


_PLACEHOLDER_JPEG_CACHE: Optional[bytes] = None


def _placeholder_jpeg() -> bytes:
    global _PLACEHOLDER_JPEG_CACHE
    if _PLACEHOLDER_JPEG_CACHE is None:
        try:
            import cv2
            import numpy as np
            frame = np.zeros((360, 640, 3), dtype="uint8")
            cv2.putText(frame, "waiting for video...", (60, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 120, 120), 2)
            ok, buf = cv2.imencode(".jpg", frame)
            _PLACEHOLDER_JPEG_CACHE = buf.tobytes() if ok else b""
        except Exception:
            _PLACEHOLDER_JPEG_CACHE = b""
    return _PLACEHOLDER_JPEG_CACHE


@app.get("/")
async def dashboard_index():
    return FileResponse(str(DASHBOARD_DIR / "index.html"))


def main() -> None:
    host = cfg_get("server", "host", default="0.0.0.0")
    port = cfg_get("server", "port", default=8000)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
