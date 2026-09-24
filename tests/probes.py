"""Synthetic probe agents used to validate that our conformance scenarios
actually discriminate.

These are test fixtures, not agents. They deliberately hardcode city names and
tool names - which is exactly what our real engine is forbidden from doing -
because their only job is to produce two traces that differ in one respect:
whether stale work is cancelled after an interruption.

If NaiveProbe (never cancels) and CancellingProbe (always cancels) score the
same on a conformance scenario, that scenario is not testing what it claims
to test, and we would rather find that out here than on the hidden set.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

_CITIES = {
    "boston": "Boston", "new york": "New York", "chicago": "Chicago",
    "miami": "Miami", "denver": "Denver", "seattle": "Seattle",
    # cities used by the adversarial-timing scenarios (conf_19-conf_23)
    "recife": "Recife", "lagos": "Lagos", "porto": "Porto", "oslo": "Oslo",
    "lima": "Lima", "accra": "Accra", "dakar": "Dakar", "nairobi": "Nairobi",
}


def _find_city(text: str) -> Optional[str]:
    low = text.lower()
    best = None
    best_pos = -1
    for key, canon in _CITIES.items():
        pos = low.rfind(key)
        if pos > best_pos:
            best_pos, best = pos, canon
    return best


class _BaseProbe:
    """Plausible-but-simple agent. Subclasses set cancel_on_interrupt."""

    cancel_on_interrupt = False

    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue) -> None:
        self.in_q = in_queue
        self.out_q = out_queue
        self.buffer: List[str] = []
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.seq = 0
        self.slots: Dict[str, Any] = {}
        self.intent: Optional[str] = None
        self.flights: List[Dict[str, Any]] = []
        self.want_book = False
        self.want_afternoon = False
        self.booked = False

    # -- plumbing -------------------------------------------------------
    async def emit(self, action: str, payload: Dict[str, Any]) -> None:
        msg: Dict[str, Any] = {"action": action, "payload": payload}
        if action in ("filler_speech", "clarification_request", "final_response"):
            msg["state_snapshot"] = {"intent": self.intent, "slots": dict(self.slots)}
        await self.out_q.put(msg)

    async def call(self, api: str, args: Dict[str, Any]) -> str:
        self.seq += 1
        call_id = "p" + str(self.seq)
        self.pending[call_id] = {"api": api, "args": args}
        await self.emit("tool_call",
                        {"call_id": call_id, "api_name": api, "args": args})
        return call_id

    async def cancel_all(self) -> None:
        for call_id in list(self.pending):
            await self.emit("cancel_tool", {"call_id": call_id})
            del self.pending[call_id]

    # -- loop -----------------------------------------------------------
    async def run(self) -> None:
        while True:
            event = await self.in_q.get()
            etype = event.get("event_type")
            payload = event.get("payload", {}) or {}
            if etype == "user_speech_chunk":
                self.buffer.append(payload.get("text", ""))
                if payload.get("end_of_turn"):
                    turn = " ".join(self.buffer).strip()
                    self.buffer = []
                    await self.on_turn(turn)
            elif etype == "interruption":
                await self.on_interrupt(payload.get("text", ""))
            elif etype == "tool_result":
                await self.on_result(payload)
            elif etype == "scenario_end":
                await self.emit("final_response", {"text": "That is everything."})

    # -- behaviour ------------------------------------------------------
    async def on_turn(self, turn: str) -> None:
        low = turn.lower()
        await self.emit("filler_speech", {"text": "Let me look that up."})
        if "book" in low or "put me on" in low:
            self.want_book = True
        if "2 pm" in low:
            self.want_afternoon = True
        if "tv" in low or "blinking" in low or "manual" in low or "toaster" in low:
            self.intent = "lookup_manual"
            await self.call("lookup_manual", {"query": turn})
            return
        city = _find_city(low)
        if city:
            self.intent = "flight_search"
            self.slots["destination"] = city
            await self.call("flight_search", {"destination": city})

    async def on_interrupt(self, text: str) -> None:
        low = text.lower()
        await self.emit("filler_speech", {"text": "Understood, adjusting now."})
        if self.cancel_on_interrupt:
            await self.cancel_all()

        if "never mind" in low or "forget it" in low:
            self.intent = "cancelled"
            return
        if "tv" in low or "blinking" in low:
            self.intent = "lookup_manual"
            self.slots.pop("destination", None)
            await self.call("lookup_manual", {"query": text})
            return
        if "2 pm" in low or "afternoon" in low:
            self.want_afternoon = True
            target = self._pick_flight()
            if target:
                self.slots["flight_id"] = target["flight_id"]
                await self.call("book_flight",
                                {"flight_id": target["flight_id"],
                                 "passenger_name": self.slots.get(
                                     "passenger_name", "Alice")})
            return
        city = _find_city(low)
        if city:
            self.slots["destination"] = city
            await self.call("flight_search", {"destination": city})

    def _pick_flight(self) -> Optional[Dict[str, Any]]:
        if not self.flights:
            return None
        if self.want_afternoon:
            for f in self.flights:
                if "2PM" in f.get("flight_id", "").upper():
                    return f
        return self.flights[0]

    async def on_result(self, payload: Dict[str, Any]) -> None:
        call_id = payload.get("call_id", "")
        info = self.pending.pop(call_id, None)
        if info is None:
            return  # cancelled call: never ground on it
        result = payload.get("result", {}) or {}
        if payload.get("status") != "success":
            await self.emit("final_response",
                            {"text": "Sorry, that did not go through."})
            return
        if "flights" in result:
            self.flights = result["flights"]
            if self.want_book and not self.booked:
                target = self._pick_flight()
                if target:
                    self.booked = True
                    self.slots["flight_id"] = target["flight_id"]
                    await self.call("book_flight",
                                    {"flight_id": target["flight_id"],
                                     "passenger_name": "Alice"})
                    return
            best = self.flights[0]
            self.slots["flight_id"] = best["flight_id"]
            await self.emit("final_response",
                            {"text": "I found " + best["flight_id"] + " for you."})
        elif "booking_id" in result:
            await self.emit("final_response",
                            {"text": "All done, reference " + result["booking_id"] + "."})
        elif "pages" in result:
            pages = result["pages"]
            if pages:
                await self.emit("final_response",
                                {"text": "The manual covers that on page "
                                         + str(pages[0]["page"]) + "."})
            else:
                await self.emit("final_response",
                                {"text": "Sorry, I could not find anything."})


class NaiveProbe(_BaseProbe):
    """Re-plans after an interruption but never cancels the superseded work."""
    cancel_on_interrupt = False


class CancellingProbe(_BaseProbe):
    """Identical, except it cancels in-flight work before re-planning."""
    cancel_on_interrupt = True
