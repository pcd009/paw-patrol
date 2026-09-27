"""Swappable output adapters. The orchestrator computes device state (led/buzzer) from active
alerts ONLY (see app/orchestrator/server.py::compute_device_state) and pushes it to every
configured sink. Tomorrow's ESP32 LED/buzzer mirror just polls GET /api/device, which is served
in plain text (`led=amber buzzer=0`) so the device needs no JSON library -- see DeviceStateSink.
"""
from __future__ import annotations

import json
from typing import Optional


class OutputSink:
    def update_device(self, led: str, buzzer: bool) -> None:
        raise NotImplementedError

    def notify(self, kind: str, payload: dict) -> None:
        """Optional: fired on new events/alerts/triage. Default: no-op."""
        pass


class ConsoleSink(OutputSink):
    """Prints one human-readable line per event / alert / Claude assessment, so the terminal
    reads as a shareable event log."""

    def __init__(self):
        self._device = None

    @staticmethod
    def _clock(iso: Optional[str] = None) -> str:
        from datetime import datetime
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone() if iso else datetime.now()
        return dt.strftime("%H:%M:%S")

    def update_device(self, led: str, buzzer: bool) -> None:
        if (led, buzzer) != self._device:  # only log changes
            self._device = (led, buzzer)
            print(f"{self._clock()}  DEVICE   led={led} buzzer={'on' if buzzer else 'off'}", flush=True)

    def notify(self, kind: str, payload: dict) -> None:
        if kind == "event":
            from datetime import datetime
            start = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(payload["ended_at"].replace("Z", "+00:00"))
            det = payload.get("evidence", {}).get("detector", "")
            how = "claude" if ("vision_strip" in det and "mock" not in det) else det.split("+")[-1] or "?"
            print(f"{self._clock(payload['started_at'])}  EVENT    {payload['label']:<15} "
                  f"{(end - start).total_seconds():5.1f}s  {payload['source']:<6} {how:<16} "
                  f"conf {payload['confidence']:.2f}", flush=True)
        elif kind == "alert":
            print(f"{self._clock(payload['triggered_at'])}  ALERT    {payload['severity']}: "
                  f"{payload['message']}", flush=True)
        elif kind == "triage":
            who = "mock" if payload.get("model") == "mock" else "claude"
            print(f"{self._clock(payload['created_at'])}  ASSESS   [{who}] {payload['decision']}: "
                  f"{payload['owner_message']}", flush=True)
        else:
            print(f"{self._clock()}  {kind.upper():<8} {json.dumps(payload)[:200]}", flush=True)


class DeviceStateSink(OutputSink):
    """The canonical source of truth for GET /api/device. Holds state in memory; the FastAPI
    route formats it as plain text. This is what a real ESP32 LED/buzzer mirror would poll."""

    def __init__(self):
        self.led = "green"
        self.buzzer = False

    def update_device(self, led: str, buzzer: bool) -> None:
        self.led = led
        self.buzzer = buzzer

    def as_plain_text(self) -> str:
        return f"led={self.led} buzzer={1 if self.buzzer else 0}"


class SerialSink(OutputSink):
    """Stub: write device state to a USB-serial ESP32 instead of (or in addition to) it polling
    HTTP. Needs `pip install pyserial`. Kept minimal: one line per update, e.g. "red 1\\n"."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self._ser = None

    def _ensure_open(self):
        if self._ser is None:
            import serial  # type: ignore
            self._ser = serial.Serial(self.port, self.baud, timeout=1)
        return self._ser

    def update_device(self, led: str, buzzer: bool) -> None:
        try:
            ser = self._ensure_open()
            ser.write(f"{led} {1 if buzzer else 0}\n".encode())
        except Exception as e:
            print(f"[SerialSink] write failed (hardware not connected?): {e}")


class WebhookSink(OutputSink):
    """Stub: POST device/alert updates to an arbitrary URL (e.g. a Slack webhook or a second
    service). Failures are swallowed -- this must never take the demo down."""

    def __init__(self, url: str, timeout_s: float = 2.0):
        self.url = url
        self.timeout_s = timeout_s

    def update_device(self, led: str, buzzer: bool) -> None:
        self._post({"type": "device", "led": led, "buzzer": buzzer})

    def notify(self, kind: str, payload: dict) -> None:
        self._post({"type": kind, **payload})

    def _post(self, body: dict) -> None:
        try:
            import requests
            requests.post(self.url, json=body, timeout=self.timeout_s)
        except Exception as e:
            print(f"[WebhookSink] post failed: {e}")


class CollarLedSink(OutputSink):
    """Mirrors the rule state on the PawPatrol collar LED: green=CALM, amber=ATTN, red=ALERT.
    Sends the collar's documented UDP command to collar_ip:cmd_port from its live.json. Only rules
    drive this (via the device state) -- never an LLM call."""

    STATE = {"green": "CALM", "amber": "ATTN", "red": "ALERT"}

    def __init__(self, collar_session: str):
        self.collar_session = collar_session
        self._last = None

    def update_device(self, led: str, buzzer: bool) -> None:
        cmd = self.STATE.get(led, "CALM")
        if cmd == self._last:
            return
        try:
            import socket
            from app.io.collar import read_live, resolve_session
            live = read_live(resolve_session(self.collar_session))
            if not live or not live.get("collar_ip"):
                return  # collar not connected yet; retry on the next change
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
                cmd.encode(), (live["collar_ip"], int(live.get("cmd_port", 4211))))
            self._last = cmd
            print(f"[collar] LED -> {cmd}", flush=True)
        except Exception as e:
            print(f"[collar] LED update failed: {e}", flush=True)


def build_sinks(names, serial_port: Optional[str] = None, webhook_url: Optional[str] = None,
                collar_session: Optional[str] = None):
    """config.yaml adapters.output_sinks -> list[OutputSink]. Returns (sinks, device_state_sink)
    so the orchestrator can also read .led/.buzzer/.as_plain_text() directly for GET /api/device."""
    sinks = []
    device_sink: Optional[DeviceStateSink] = None
    for name in names:
        if name == "console":
            sinks.append(ConsoleSink())
        elif name == "device_state":
            device_sink = DeviceStateSink()
            sinks.append(device_sink)
        elif name == "serial" and serial_port:
            sinks.append(SerialSink(serial_port))
        elif name == "webhook" and webhook_url:
            sinks.append(WebhookSink(webhook_url))
    if collar_session:
        sinks.append(CollarLedSink(collar_session))
    if device_sink is None:
        # /api/device must always have something to read from.
        device_sink = DeviceStateSink()
        sinks.append(device_sink)
    return sinks, device_sink
