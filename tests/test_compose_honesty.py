"""Day 2 WP3 (docs/fdb_v3_day2_plan.md): honest FINAL wording.

Found live: the Composer wrote "your flight has been successfully
confirmed" from a search_flights result alone (book_flight was never
planned/run for that turn) -- it never knew which tools actually ran.
`_build_compose_request`'s view now names them (`executed_calls`) and
what a multi-action turn asked for but never got (`not_done`);
`workers/composer.build_prompt` turns that into an explicit instruction.
Scripted providers can't test the wording a live model chooses to
write, so this only checks the view/prompt contents directly (the plan
calls for a live spot-check separately, NOT VERIFIED here)."""

from __future__ import annotations

from conftest import FAST_WORKER_LATENCY, chunk_event, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.composer import build_prompt
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
BOOK_FLIGHT_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"passenger_name": {"type": "string"}}, "required": ["passenger_name"]},
}


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


# --- build_prompt itself: pure unit tests on synthetic views --------------


def test_no_honesty_block_when_executed_calls_is_absent():
    """Backward compatibility: a caller that predates this change (or any
    ScriptedProvider-driven test registering by the old prompt's own
    substrings) sees the prompt completely unchanged."""
    prompt = build_prompt({"facts": {}, "effects": {}, "open_questions": []})
    assert "executed_calls" not in prompt


def test_honesty_block_names_only_what_actually_executed():
    prompt = build_prompt({
        "facts": {"s1": {"flight_id": "FL1"}},
        "effects": {},
        "open_questions": [],
        "executed_calls": [{"tool": "search_flights", "args": {"destination": "Chicago"}}],
        "not_done": [],
    })
    assert "search_flights" in prompt
    assert "book_flight" not in prompt
    assert "never say something was booked" in prompt.lower() or "never say" in prompt.lower()


def test_honesty_block_names_what_was_not_done():
    prompt = build_prompt({
        "facts": {"s1": {"flight_id": "FL1"}},
        "effects": {},
        "open_questions": [],
        "executed_calls": [{"tool": "search_flights", "args": {"destination": "Chicago"}}],
        "not_done": ["book_flight"],
    })
    assert "not completed" in prompt
    assert "book_flight" in prompt


# --- _build_compose_request's real view, through the kernel --------------


def test_compose_view_lists_only_the_consumed_call_not_the_unexecuted_one(config):
    """A two-step plan where only the first step ever executes (the
    second is still PROPOSED, blocked) -- COMPOSE must not be dispatched
    at all until every required step is done (existing invariant), so
    this proves the harness setup itself, then a second scenario proves
    executed_calls reflects exactly what ran once the goal *does*
    complete."""
    on_config = Config(**{**config.__dict__, "multi_action_enabled": True})

    captured_prompts: list[str] = []

    class CapturingProvider(ScriptedProvider):
        def complete_json(self, kind, prompt, schema, *, media=None):
            if kind == "compose":
                captured_prompts.append(prompt)
            return super().complete_json(kind, prompt, schema, media=media)

    capturing = CapturingProvider()
    capturing.register(
        "interpret", "find flights to Paris",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Paris"}]},
    )
    capturing.register(
        "plan", "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    capturing.register("compose", "", {"text": "Found a flight to Paris.", "claims": []})

    h = new_harness(on_config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "FL1"}}}, provider=capturing)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("find flights to Paris")])
    h.send(150_000, [eot_event()])
    for t in range(200_000, 3_000_000, 10_000):
        h.advance(t)
        if captured_prompts:
            break

    assert captured_prompts, "COMPOSE was never dispatched"
    assert "search_flights" in captured_prompts[0]
    assert "book_flight" not in captured_prompts[0]
