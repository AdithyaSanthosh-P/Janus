"""A slow model call, as live on 21 Oct (one INTERPRET took 23.7 s):
the user hears "Still working on it." instead of silence, and an answer given
much later is not folded into the turn still being understood."""

from __future__ import annotations

import asyncio

from conftest import chunk_event, drain, eot_event, manifest_event

from config.templates import SLOW_INTERPRET
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider


async def _no_sleep(_s):
    return None


def setup(interpret_ms: int):
    mock = MockSmartThings()
    asyncio.run(mock.command("lock-1", "lock", "unlock"))
    mock.commands.clear()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "no leave it", {"act": "deny", "slot_deltas": []})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: asyncio.run(execute(n, args)))}
             for m in MANIFEST}
    latency = {kind: 10_000 for kind in JobKind}
    latency[JobKind.INTERPRET] = interpret_ms * 1000
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=latency)
    h.send(0, [manifest_event(MANIFEST)])
    return h, mock


def push(h, ts, events, until):
    return [er.action for er in h.send(ts, events).emit_report.emitted] + drain(h, until, stop_on_final=False)


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def said(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
            and getattr(a.body, "text", None)]


def offer(h, ts):
    return push(h, ts, [{"type": "offer", "payload": {"offer_id": "o-1", "text": "The front door is unlocked. Shall I lock it?",
                                                       "tool": "st_lock", "args": {"device": "front door"}}}], ts + 1_000_000)


def test_a_slow_interpretation_is_announced_once():
    h, _ = setup(interpret_ms=9_000)
    actions = say(h, 100_000, "no leave it", 12_000_000)
    assert said(actions).count(SLOW_INTERPRET) == 1
    assert said(actions).index(SLOW_INTERPRET) == 0  # before anything else
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_fast_interpretation_says_nothing_extra():
    h, _ = setup(interpret_ms=500)
    actions = say(h, 100_000, "no leave it", 4_000_000)
    assert SLOW_INTERPRET not in said(actions)


def test_a_later_answer_is_not_folded_into_a_turn_still_being_understood():
    """The live failure: "No, leave it." (interpretation slow), then 20 s
    later "Yes, please." was merged into it and accepted the declined offer."""
    h, mock = setup(interpret_ms=25_000)
    offer(h, 100_000)
    say(h, 2_000_000, "no leave it", 3_000_000)
    actions = say(h, 22_000_000, "yes please", 60_000_000)
    assert mock.commands == []  # the declined offer never ran
    assert "Okay, nothing was sent." in said(actions)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_sentence_split_by_a_short_pause_is_still_merged():
    h, _ = setup(interpret_ms=3_000)
    offer(h, 100_000)
    h.send(2_000_000, [chunk_event("no")])
    h.send(2_050_000, [eot_event()])
    h.send(3_000_000, [chunk_event("leave it")])
    h.send(3_050_000, [eot_event()])
    merged = h.store.facts.get("turn.t-0002.merged")
    assert merged is not None and merged.value == ["t-0001"]


def test_the_benchmark_profile_is_unchanged():
    cfg = fdb_v3_config()
    assert cfg.merge_split_turns_max_gap_ms == 0 and cfg.slow_interpret_notice_ms == 0
