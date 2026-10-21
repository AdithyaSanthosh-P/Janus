"""A final answer may not deny what actually happened (live, 21 Oct): the lock
command was confirmed by SmartThings (`done: true, state: locked`) and the
composer still wrote "I attempted to lock the front door, but the operation
failed to complete." With Config.final_contradiction_guard_enabled, a goal
whose writes all confirmed gets a final built from what ran instead."""

from __future__ import annotations

import asyncio
import dataclasses

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel.replies import claims_failure
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}
LOCK = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}
FALSE_FAILURE = "I attempted to lock the front door, but the operation failed to complete."


async def _no_sleep(_s):
    return None


def run_lock(compose_text: str, *, config=None, mock=None):
    mock = mock or MockSmartThings()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "lock the front", LOCK)
    provider.register("compose", "", {"text": compose_text, "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: asyncio.run(execute(n, args)))}
             for m in MANIFEST}
    cfg = config or dataclasses.replace(demo_config(), frame_rendering_enabled=False)
    h = SimHarness(cfg, seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    h.send(100_000, [chunk_event("lock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 6_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return finals[0].body, mock


def test_a_confirmed_write_is_never_reported_as_failed():
    body, mock = run_lock(FALSE_FAILURE)
    # Device commands are now said from their results in demo mode
    # (replies.device_write_final), which also covers this case.
    assert body.text == "The front door is now locked."
    assert body.task_completed is True
    assert len(mock.commands) == 1


def test_a_correct_final_is_left_alone():
    body, _ = run_lock("The front door is now locked.")
    assert body.text == "The front door is now locked."


def test_a_write_that_did_not_confirm_keeps_its_honest_line():
    body, _ = run_lock("I have locked the front door.", mock=MockSmartThings(stuck={"lock"}))
    assert "couldn't confirm" in body.text and body.task_completed is False


def test_the_guard_is_demo_only():
    assert demo_config().final_contradiction_guard_enabled is True
    assert fdb_v3_config().final_contradiction_guard_enabled is False
    assert Config().final_contradiction_guard_enabled is False
    off = dataclasses.replace(demo_config(), frame_rendering_enabled=False, final_contradiction_guard_enabled=False,
                              demo_speech_polish_enabled=False)
    body, _ = run_lock(FALSE_FAILURE, config=off)
    assert body.text == FALSE_FAILURE  # today's behaviour without the guard


def test_failure_wording_is_recognised():
    for text in (FALSE_FAILURE, "I couldn't lock the front door.", "Sorry, I was unable to set the AC.",
                 "The lock command did not go through.", "Unfortunately that didn't work.", "There was an error locking it."):
        assert claims_failure(text), text
    for text in ("The front door is now locked.", "Done. The AC is set to 21 degrees.", "I locked the front door for you."):
        assert not claims_failure(text), text


def test_the_guard_alone_replaces_a_false_failure_with_what_ran():
    """The guard's own wording, with the device-command final switched off."""
    guard_only = dataclasses.replace(demo_config(), frame_rendering_enabled=False, demo_speech_polish_enabled=False)
    body, _ = run_lock(FALSE_FAILURE, config=guard_only)
    assert body.text == "Done: Janus Lock is now locked."
