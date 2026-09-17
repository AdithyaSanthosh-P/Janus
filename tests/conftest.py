from __future__ import annotations

import pytest

from prism_rt.config import Config

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
        "required": ["destination"],
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


def manifest_event(tools: list[dict]) -> dict:
    return {"type": "manifest", "payload": {"tools": tools}}


def tool_result_event(call_id: str, *, status: str = "ok", result=None, error=None) -> dict:
    return {
        "type": "tool_result",
        "payload": {"call_id": call_id, "status": status, "result": result, "error": error},
    }


@pytest.fixture
def config() -> Config:
    return Config()
