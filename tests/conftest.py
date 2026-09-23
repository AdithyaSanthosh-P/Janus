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


def frame_event(frame_id: str) -> dict:
    return {"type": "video_frame", "payload": {"frame_id": frame_id}}


def audio_clip_event(clip_id: str) -> dict:
    return {"type": "audio_clip", "payload": {"clip_id": clip_id}}


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


def drain_liveness_only(harness, *, max_iterations: int = 50, stop_on_final: bool = True) -> tuple[list, int]:
    """P0.1 (C9c): like `drain`, but advances *only* via
    `SimHarness.fire_liveness_if_due()` -- never a blind fixed-`step_us`
    tick. Proves a scenario can make progress on TimerWheel liveness alone
    (T-05), the same "harness sends nothing more" shape D1 was found under
    (`docs/original_design_audit.md`). Returns (actions, liveness_fire_count);
    stops once no timer is due (nothing left to wait for) or `max_iterations`
    is hit, whichever comes first."""
    actions = []
    fires = 0
    for _ in range(max_iterations):
        report = harness.fire_liveness_if_due()
        if report is None:
            break
        fires += 1
        for er in report.emit_report.emitted:
            actions.append(er.action)
            if stop_on_final and er.action.action_type.value == "final":
                return actions, fires
    return actions, fires


@pytest.fixture
def config() -> Config:
    """V0/V1 test files were written and timed against V1-era semantics
    (writes admitted as soon as CommitGate's G1-G9 pass, no settle delay).
    DEFAULT_CONFIG's V2 flags default True now that V2 is frozen, so this
    fixture pins them back off explicitly — decoupling test_v0.py/test_v1.py
    from the global default rather than baking V2 timing into tests that
    were never designed to exercise it. test_v2.py constructs its own
    Config(...) per scenario instead of using this fixture."""
    return Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
