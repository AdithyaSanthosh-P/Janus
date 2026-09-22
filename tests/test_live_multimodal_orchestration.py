"""Regression tests for a second live-model reliability gap, found by
actually running `demo/run_live_multimodal_demo.py` against the real
Gemini API (not by design review): a live model correctly recognized a
vision-grounded question (`visual_reference="at_utterance"`) but had no
guidance on what to name `visual_candidates`, so it left them empty.
Empty candidates make `kernel/perception.py` fall back to
`_GENERIC_TARGETS` (`device_model`/`visible_state` -- built for this
project's own tested device-diagnostic scenario, N-05), which is wrong
for any other visual question and gets a legitimately empty claim result
back from VISION. Separately, a live PLAN call had no way to know a step
could legitimately bind to a slot fact that doesn't exist yet -- one a
pending vision question will fill in once it resolves -- so it either
invented a literal from the transcript or left the parameter unbound,
bypassing the vision claim entirely.

Two fixes, tested independently here, no real network or sleeps:
1. `workers/interpreter.py.build_prompt` -- when `vision_enabled` is set,
   the prompt now explains `visual_reference`/`visual_candidates` and
   ties candidate naming to the same tool-parameter vocabulary the
   existing `slot_deltas` guidance already uses.
2. `kernel/task.py._build_plan_request` -- when a vision question is open
   and unanswered, its target names/descriptions are passed into the
   PLAN job's view (`pending_visual_targets`); `workers/planner.py.
   build_prompt` renders them as an explicit "these facts don't exist
   yet but will" instruction.

Confirmed live after both fixes landed: `demo/run_live_multimodal_demo.py`
correctly named `top_color`/`bottom_color`, waited for and bound
`color_meaning_lookup` to the resulting slot facts, and produced a FINAL
grounded in the real image -- not just replayed here since that needs a
real API key, but the deterministic wiring these tests cover is exactly
what made that possible.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, frame_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
from prism_rt.workers.interpreter import build_prompt as interpret_build_prompt
from prism_rt.workers.planner import build_prompt as plan_build_prompt

COLOR_LOOKUP_TOOL = {
    "name": "color_meaning_lookup",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"top_color": {"type": "string"}, "bottom_color": {"type": "string"}},
        "required": ["top_color", "bottom_color"],
    },
}


def v3_config() -> Config:
    return Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        vision_enabled=True,
    )


# --- unit tests: build_prompt in isolation --------------------------------


def test_interpret_prompt_explains_visual_candidates_when_vision_enabled():
    view = {
        "transcript": "what colors do you see",
        "active_intent": None,
        "active_slots": {},
        "suspended_goals": [],
        "pending_clarification": None,
        "tools": [],
        "vision_enabled": True,
    }
    prompt = interpret_build_prompt(view)
    assert "visual_candidates" in prompt
    assert "visual_reference" in prompt


def test_interpret_prompt_omits_visual_block_when_vision_disabled():
    view = {
        "transcript": "book me a flight",
        "active_intent": None,
        "active_slots": {},
        "suspended_goals": [],
        "pending_clarification": None,
        "tools": [],
        "vision_enabled": False,
    }
    prompt = interpret_build_prompt(view)
    assert "visual_candidates" not in prompt
    # No `vision_enabled` key at all (a caller/test that predates this fix)
    # must behave the same way -- additive only.
    del view["vision_enabled"]
    assert "visual_candidates" not in interpret_build_prompt(view)


def test_plan_prompt_names_pending_visual_targets():
    view = {
        "intent": "device_help",
        "facts": {},
        "catalog": [],
        "change_context": None,
        "pending_visual_targets": [{"name": "top_color", "description": "the color at the top"}],
    }
    prompt = plan_build_prompt(view)
    assert "top_color" in prompt
    assert 'slot.$G.<name>' in prompt


def test_plan_prompt_omits_visual_block_when_no_pending_targets():
    view = {"intent": "search_flights", "facts": {"destination": "Pune"}, "catalog": [], "change_context": None, "pending_visual_targets": []}
    prompt = plan_build_prompt(view)
    assert "pending" not in prompt.lower()
    # Missing key entirely (predates this fix) must not crash either.
    del view["pending_visual_targets"]
    assert plan_build_prompt(view)  # no exception


# --- integration test: the real wiring, end to end (still ScriptedProvider) --


def test_pending_visual_targets_actually_reach_the_plan_prompt():
    """Proves `kernel/task.py._build_plan_request` really does propagate an
    open question's targets into the PLAN job's view -- not just that
    `planner.build_prompt` renders them correctly in isolation given a
    hand-built dict. Uses a callable ScriptedProvider response to capture
    the real prompt the kernel assembled."""
    config = v3_config()
    captured: list[str] = []

    def capture_plan(prompt: str) -> dict:
        captured.append(prompt)
        return {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "color_meaning_lookup",
                    "kind": "read",
                    "bindings": {
                        "top_color": {"type": "fact", "key": "slot.$G.top_color"},
                        "bottom_color": {"type": "fact", "key": "slot.$G.bottom_color"},
                    },
                    "after": [],
                }
            ]
        }

    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what colors do you see",
        {
            "act": "new_goal",
            "intent": "color_help",
            "visual_reference": "at_utterance",
            "visual_candidates": [
                {"name": "top_color", "description": "the color at the top"},
                {"name": "bottom_color", "description": "the color at the bottom"},
            ],
        },
    )
    provider.register("plan", "color_help", capture_plan)
    provider.register(
        "vision",
        "top_color",
        {
            "claims": [
                {"name": "top_color", "value": "red", "confidence": "high"},
                {"name": "bottom_color", "value": "blue", "confidence": "high"},
            ]
        },
    )
    provider.register("compose", "s1", {"text": "Red on top, blue on the bottom.", "claims": ["result:s1"]})

    h = SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"color_meaning_lookup": {"latency_ms": 50, "response": {"meaning": "a calibration stripe"}}},
    )
    h.send(0, [manifest_event([COLOR_LOOKUP_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what colors do you see")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Red on top, blue on the bottom."

    assert len(captured) == 1
    assert "top_color" in captured[0]
    assert "bottom_color" in captured[0]
