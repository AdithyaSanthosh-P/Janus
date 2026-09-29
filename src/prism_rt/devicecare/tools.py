"""Mock device-care services: three lookups, a warranty check and two writes.

`DeviceCareToolset` is the whole extension's tool layer: `manifest` is what the
kernel is told about (mutability declared, so no benchmark-style exemptions are
needed), `make_executor()` is what the host calls when the kernel emits a
TOOL_CALL. State lives on the toolset instance -- one per session -- so a
booking made in a conversation is visible to the next tool call and to tests.

The write tools are idempotent by design of the *kernel*, not of this mock:
the CommitGate never emits a second booking for the same plan step, so if two
ever appear in `bookings` something upstream is broken.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable

_KB_PATH = Path(__file__).with_name("kb.json")


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _device_key(kb: dict, raw) -> str | None:
    text = _norm(raw)
    for key, dev in kb["devices"].items():
        if text == key or text in _norm(dev["name"]) or key in text:
            return key
    aliases = {"wifi router": "router", "modem": "router", "washing machine": "washer", "washing": "washer", "laundry": "washer"}
    for alias, key in aliases.items():
        if alias in text:
            return key
    return None


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


MANIFEST: list[dict] = [
    {
        "name": "identify_indicator",
        "description": "Explain what a device indicator light means, from its colour and whether it is solid, blinking or off.",
        "mutability": "read_only",
        "params_schema": _obj(
            {
                "device_type": _str("The kind of device: router or washer."),
                "led_color": _str("Colour of the light: green, orange, red or off."),
                "led_state": _str("solid, blinking or off."),
                "led_name": _str("Which light it is: power, internet or wifi. Visible on the device, so read it from the camera picture."),
            },
            ["device_type", "led_color", "led_state", "led_name"],
        ),
    },
    {
        "name": "lookup_error_code",
        "description": "Explain an error code shown on a device's display.",
        "mutability": "read_only",
        "params_schema": _obj(
            {"device_type": _str("The kind of device: router or washer."), "code": _str("The code on the display, for example E3.")},
            ["device_type", "code"],
        ),
    },
    {
        "name": "get_fix_steps",
        "description": "Step-by-step self-repair instructions for a diagnosed issue. Use the issue_id a diagnosis returned.",
        "mutability": "read_only",
        "params_schema": _obj({"issue_id": _str("Optional. The issue_id from a diagnosis; defaults to the issue diagnosed most recently in this conversation.")}, []),
    },
    {
        "name": "check_warranty",
        "description": "Check whether a device is still under warranty, by serial number.",
        "mutability": "read_only",
        "params_schema": _obj({"serial_number": _str("The device serial number, for example SN-R500-1042.")}, ["serial_number"]),
    },
    {
        "name": "book_technician",
        "description": "Book ONE technician home visit for a diagnosed issue. Only when the user has asked to book.",
        "mutability": "state_changing",
        "params_schema": _obj(
            {
                "date": _str("The day of the visit, for example Friday or 2026-10-02."),
                "time_slot": _str("morning, afternoon or evening."),
                "issue_id": _str("Optional. Defaults to the issue diagnosed most recently in this conversation."),
            },
            ["date", "time_slot"],
        ),
    },
    {
        "name": "open_support_ticket",
        "description": "Open a support ticket for a diagnosed issue. Only when the user has asked for a ticket.",
        "mutability": "state_changing",
        "params_schema": _obj(
            {
                "priority": _str("low, normal or urgent."),
                "issue_id": _str("Optional. Defaults to the issue diagnosed most recently in this conversation."),
            },
            ["priority"],
        ),
    },
]


class DeviceCareToolset:
    def __init__(self, kb: dict | None = None) -> None:
        self.kb = kb if kb is not None else json.loads(_KB_PATH.read_text())
        self.manifest: list[dict] = MANIFEST
        self.bookings: list[dict] = []
        self.tickets: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.current_issue: str | None = None  # the open case: set by a diagnosis

    # ---- executor -----------------------------------------------------
    def make_executor(self) -> Callable[[str, dict], Awaitable[dict]]:
        async def execute(tool_name: str, args: dict) -> dict:
            self.calls.append((tool_name, dict(args)))
            handler = getattr(self, f"_{tool_name}", None)
            if handler is None:
                raise ValueError(f"unknown tool {tool_name!r}")
            return handler(args)

        return execute

    def _issue(self, a: dict) -> str | None:
        """The issue a call is about: the one it names, else the open case."""
        issue = str(a.get("issue_id") or "").strip() or self.current_issue
        return issue if issue in self.kb["issues"] else None

    # ---- reads --------------------------------------------------------
    def _identify_indicator(self, a: dict) -> dict:
        dev = _device_key(self.kb, a.get("device_type"))
        if dev is None or "indicators" not in self.kb["devices"][dev]:
            return {"found": False, "message": f"No indicator guide for {a.get('device_type')!r}."}
        colour, state = _norm(a.get("led_color")), _norm(a.get("led_state"))
        name = _norm(a.get("led_name"))
        scored = []
        for ind in self.kb["devices"][dev]["indicators"]:
            score = (ind["color"] == colour) * 2 + (ind["state"] == state) * 2 + (bool(name) and ind["led"] == name) * 3
            if ind["color"] == colour or ind["state"] == state:
                scored.append((score, ind))
        if not scored:
            return {"found": False, "message": "That light pattern is not in the guide."}
        top = max(score for score, _ in scored)
        tied = [ind for score, ind in scored if score == top]
        if len({ind["issue"] for ind in tied}) > 1:
            lights = sorted({ind["led"] for ind in tied})
            return {"found": False, "message": f"That pattern appears on more than one light ({', '.join(lights)}). Which light is it?"}
        ind = tied[0]
        self.current_issue = ind["issue"]
        return {"found": True, "device": self.kb["devices"][dev]["name"], "led": ind["led"], "meaning": ind["meaning"],
                "technician_may_be_needed": self.kb["issues"][ind["issue"]]["technician_needed"]}

    def _lookup_error_code(self, a: dict) -> dict:
        dev = _device_key(self.kb, a.get("device_type"))
        if dev is None or "error_codes" not in self.kb["devices"][dev]:
            return {"found": False, "message": f"No error-code list for {a.get('device_type')!r}."}
        code = str(a.get("code", "")).strip().upper().replace(" ", "")
        entry = self.kb["devices"][dev]["error_codes"].get(code)
        if entry is None:
            return {"found": False, "message": f"Code {code} is not in the list for the {self.kb['devices'][dev]['name']}."}
        self.current_issue = entry["issue"]
        return {"found": True, "device": self.kb["devices"][dev]["name"], "code": code, "meaning": entry["meaning"],
                "technician_may_be_needed": self.kb["issues"][entry["issue"]]["technician_needed"]}

    def _get_fix_steps(self, a: dict) -> dict:
        key = self._issue(a)
        if key is None:
            return {"found": False, "message": "Nothing has been diagnosed yet. Show me the device or tell me its error code first."}
        issue = self.kb["issues"][key]
        return {"found": True, "title": issue["title"], "steps": issue["fix_steps"], "technician_needed": issue["technician_needed"]}

    def _check_warranty(self, a: dict) -> dict:
        serial = str(a.get("serial_number", "")).strip().upper()
        rec = self.kb["warranty"].get(serial)
        if rec is None:
            return {"found": False, "message": f"No warranty record for serial {serial}."}
        return {"found": True, "serial_number": serial, "status": rec["status"], "expires": rec["expires"]}

    # ---- writes -------------------------------------------------------
    def _book_technician(self, a: dict) -> dict:
        issue_id = self._issue(a)
        if issue_id is None:
            return {"booked": False, "message": "Nothing has been diagnosed yet, so there is nothing to book a technician for."}
        slot = _norm(a.get("time_slot"))
        if slot not in self.kb["technician_slots"]:
            return {"booked": False, "message": f"Slot must be one of {', '.join(self.kb['technician_slots'])}."}
        booking = {"booking_id": f"BK-{len(self.bookings) + 1:04d}", "issue_id": issue_id, "date": a.get("date"), "time_slot": slot}
        self.bookings.append(booking)
        return {"booked": True, **{k: v for k, v in booking.items() if k != "issue_id"}}

    def _open_support_ticket(self, a: dict) -> dict:
        issue_id = self._issue(a)
        if issue_id is None:
            return {"opened": False, "message": "Nothing has been diagnosed yet, so there is nothing to open a ticket for."}
        ticket = {"ticket_id": f"TK-{len(self.tickets) + 1:04d}", "issue_id": issue_id, "priority": _norm(a.get("priority")) or "normal"}
        self.tickets.append(ticket)
        return {"opened": True, **{k: v for k, v in ticket.items() if k != "issue_id"}}
