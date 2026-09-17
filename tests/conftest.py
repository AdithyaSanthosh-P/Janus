from __future__ import annotations

import pytest

from prism_rt.config import Config
from prism_rt.model.types import JobKind

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}, "date": {"type": "string"}},
        "required": ["destination"],
    },
}

GET_SEAT_MAP_TOOL = {
    "name": "get_seat_map",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}

BOOK_FLIGHT_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}

CREATE_TICKET_TOOL = {
    "name": "create_ticket",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"issue": {"type": "string"}},
        "required": ["issue"],
    },
}

# Fast, deterministic worker latency for every V1 test — real timing
# behavior is exercised through tool latency instead, which is what the
# interruption scenarios actually care about.
FAST_WORKER_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}


def manifest_event(tools: list[dict]) -> dict:
    return {"type": "manifest", "payload": {"tools": tools}}


def tool_result_event(call_id: str, *, status: str = "ok", result=None, error=None) -> dict:
    return {
        "type": "tool_result",
        "payload": {"call_id": call_id, "status": status, "result": result, "error": error},
    }


def chunk_event(text: str) -> dict:
    return {"type": "text_chunk", "payload": {"text": text}}


def eot_event() -> dict:
    return {"type": "end_of_turn", "payload": {}}


def interruption_event() -> dict:
    return {"type": "interruption", "payload": {}}


def drain(harness, max_us: int, *, step_us: int = 10_000, stop_on_final: bool = True) -> list:
    """Advance the harness in small steps up to max_us, collecting every
    emitted Action along the way. Stops early once a FINAL is emitted (the
    common case) unless stop_on_final=False."""
    actions = []
    t = harness.clock.now_us()
    while t < max_us:
        t += step_us
        report = harness.advance(t)
        for er in report.emit_report.emitted:
            actions.append(er.action)
            if stop_on_final and er.action.action_type.value == "final":
                return actions
    return actions


@pytest.fixture
def config() -> Config:
    return Config()
