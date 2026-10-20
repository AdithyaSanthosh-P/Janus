"""Failure drills (adapters/drills.py): the faults themselves, the control
messages, and what the kernel makes of them end to end."""

from __future__ import annotations

import asyncio

import pytest
from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.adapters.drills import DrilledBackend, DrilledProvider, DrillState, run_external_command
from config.templates import INTERPRET_FAILED
from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.client import SmartThingsError
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider


def run(coro):
    return asyncio.run(coro)


async def _no_sleep(_s):
    return None


def drilled(state=None):
    state = state or DrillState()
    mock = MockSmartThings()
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)

    backend = DrilledBackend(mock, state, sleep=sleep)
    return state, mock, backend, SmartThingsToolset(backend, sleep=_no_sleep).make_executor(), slept


def drill(name, on=True, **args):
    return {"v": 1, "type": "drill", "name": name, "on": on, "args": args}


# ---- control messages ----------------------------------------------------------

def test_toggles_and_reset():
    s = DrillState()
    s.apply(drill("st_unreachable"))
    s.apply(drill("st_latency", ms=2500))
    s.apply(drill("model_slow"))
    assert s.st_unreachable and s.st_latency_ms == 2500 and s.model_slow_ms == 4000
    s.apply(drill("st_latency", on=False))
    assert s.st_latency_ms == 0
    s.apply(drill("reset"))
    assert s == DrillState() and s.active() == []


def test_delay_is_capped():
    s = DrillState()
    s.apply(drill("model_slow", ms=999_999))
    assert s.model_slow_ms == 15_000


@pytest.mark.parametrize("message", [
    {"v": 2, "type": "drill", "name": "reset", "on": True},
    {"v": 1, "type": "other", "name": "reset", "on": True},
    drill("format_disk"),
    drill("external_command", device="lock"),
    drill("st_latency", ms="soon"),
    "not a dict",
])
def test_bad_messages_are_refused_and_change_nothing(message):
    s = DrillState()
    with pytest.raises(ValueError):
        s.apply(message)
    assert s == DrillState()


def test_external_command_is_returned_not_applied():
    s = DrillState()
    out = s.apply(drill("external_command", device="Janus Lock", capability="lock", command="unlock"))
    assert out == {"device": "Janus Lock", "capability": "lock", "command": "unlock"} and s == DrillState()


# ---- SmartThings faults ----------------------------------------------------------

def test_no_drill_passes_through():
    _s, mock, _b, execute, slept = drilled()
    assert run(execute("st_lock", {"device": "front door"}))["done"] is True
    assert len(mock.commands) == 1 and slept == []


def test_unreachable_fails_before_anything_is_sent():
    s, mock, _b, execute, _ = drilled()
    s.st_unreachable = True
    with pytest.raises(SmartThingsError, match="couldn't reach SmartThings"):
        run(execute("st_lock", {"device": "front door"}))
    assert mock.commands == []


def test_no_confirm_is_reported_unconfirmed_and_the_device_never_changes():
    s, mock, backend, execute, _ = drilled()
    s.st_no_confirm = True
    out = run(execute("st_switch", {"device": "switch", "state": "on"}))
    assert out["outcome"] == "unconfirmed" and out["sent"] is True and "done" not in out
    assert mock.commands == []  # the "cloud" accepted it; the device never got it
    status = run(mock.status("switch-1"))
    assert status["components"]["main"]["switch"]["switch"]["value"] == "off"


def test_latency_delays_every_call():
    s, _m, _b, execute, slept = drilled()
    s.st_latency_ms = 3000
    run(execute("st_get_status", {"device": "lock"}))
    assert slept and set(slept) == {3.0}


def test_external_command_works_under_drills_and_skips_the_ledger():
    s, mock, backend, execute, _ = drilled()
    s.st_unreachable = True
    note = run(run_external_command(backend, {"device": "janus lock", "capability": "lock", "command": "lock"}))
    assert "Janus Lock" in note and mock.commands == [("lock-1", "lock", "lock", [])]
    s.st_unreachable = False
    out = run(execute("st_lock", {"device": "front door"}))
    assert out["already"] is True and len(mock.commands) == 1  # nothing sent a second time


def test_external_command_needs_one_matching_device():
    _s, _m, backend, _e, _ = drilled()
    with pytest.raises(ValueError):
        run(run_external_command(backend, {"device": "janus", "capability": "lock", "command": "lock"}))


# ---- model faults ----------------------------------------------------------------

def test_model_slow_and_down():
    slept: list[float] = []
    inner = ScriptedProvider()
    inner.register("interpret", "", {"act": "chitchat"})
    s = DrillState()
    p = DrilledProvider(inner, s, sleep=slept.append)
    assert p.complete_json("interpret", "hi", {}) == {"act": "chitchat"} and slept == []
    s.model_slow_ms = 4000
    p.complete_json("interpret", "hi", {})
    assert slept == [4.0]
    s.model_down = True
    with pytest.raises(OSError):
        p.complete_json("interpret", "hi", {})


# ---- end to end through the kernel ---------------------------------------------------

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}


def demo(**kw):
    base = dict(action_plans_enabled=True, multi_action_enabled=True, conversational_replies_enabled=True,
                confirmation_policy_enabled=True, unconfirmed_outcomes_enabled=True,
                settle_barrier_enabled=True, settle_ms=500)
    base.update(kw)
    return Config(**base)


def e2e(state, interpret, utterance, *, more=(), config=None):
    mock = MockSmartThings()
    execute = SmartThingsToolset(DrilledBackend(mock, state, sleep=_no_sleep), sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    for key, reply in interpret:
        provider.register("interpret", key, reply)
    provider.register("compose", "", {"text": "Done.", "claims": []})
    sent = []

    def handler_for(name):
        def handler(args):
            sent.append((name, dict(args)))
            return run(execute(name, args))
        return handler

    tools = {m["name"]: {"latency_ms": 200, "handler": handler_for(m["name"])} for m in MANIFEST}
    h = SimHarness(config or demo(), seed=1, provider=DrilledProvider(provider, state, sleep=lambda _s: None),
                   tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    h.send(100_000, [chunk_event(utterance)])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 4_000_000, stop_on_final=False)
    t = 4_200_000
    for text in more:
        h.send(t, [chunk_event(text)])
        actions += [er.action for er in h.send(t + 50_000, [eot_event()]).emit_report.emitted]
        actions += drain(h, t + 4_000_000, stop_on_final=False)
        t += 4_500_000
    return h, mock, sent, actions


def spoken(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
            and getattr(a.body, "text", None)]


def finals(actions):
    return [a for a in actions if a.action_type == ActionType.FINAL]


SWITCH_ON = {"act": "new_goal", "intent": "st_switch", "slot_deltas": [], "commit_intent": True,
             "actions": [{"tool": "st_switch", "args": {"device": "switch", "state": "on"}}]}


def test_e2e_no_confirm_never_claims_done():
    s = DrillState(st_no_confirm=True)
    h, mock, sent, actions = e2e(s, [("switch", SWITCH_ON)], "turn on the switch")
    assert [n for n, _ in sent] == ["st_switch"] and mock.commands == []
    assert not any(getattr(f.body, "task_completed", False) for f in finals(actions))
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_e2e_model_down_says_so_and_does_nothing():
    s = DrillState(model_down=True)
    h, mock, sent, actions = e2e(s, [("switch", SWITCH_ON)], "turn on the switch", config=demo_config())
    assert sent == [] and mock.commands == []
    assert spoken(actions) == [INTERPRET_FAILED]  # once, not silence
    assert not any(getattr(f.body, "task_completed", False) for f in finals(actions))
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_failure_notice_is_demo_only():
    assert demo_config().interpret_failure_notice_enabled is True
    assert fdb_v3_config().interpret_failure_notice_enabled is False
    assert Config().interpret_failure_notice_enabled is False


def test_e2e_someone_else_unlocks_while_janus_waits_for_yes():
    """The `external_command` drill on stage: "Unlock the front door" is a
    security write (G12), so Janus asks first. Someone else unlocks it in the
    meantime; the user's "yes" then finds it already unlocked and nothing is
    sent -- the write re-reads the device before it acts."""
    s = DrillState()
    mock = MockSmartThings()
    backend = DrilledBackend(mock, s, sleep=_no_sleep)
    execute = SmartThingsToolset(backend, sleep=_no_sleep).make_executor()
    run(mock.command("lock-1", "lock", "lock"))  # the door starts locked
    mock.commands.clear()
    provider = ScriptedProvider()
    # Most specific first: the interpret prompt carries the earlier turns.
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "unlock the front", {"act": "new_goal", "intent": "st_unlock", "slot_deltas": [], "commit_intent": True,
                                                        "actions": [{"tool": "st_unlock", "args": {"device": "front door"}}]})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    results = []

    def handler_for(name):
        def handler(args):
            out = run(execute(name, args))
            results.append((name, out))
            return out
        return handler

    tools = {m["name"]: {"latency_ms": 200, "handler": handler_for(m["name"])} for m in MANIFEST}
    h = SimHarness(demo(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    h.send(100_000, [chunk_event("unlock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 3_000_000, stop_on_final=False)
    assert any("security action" in t for t in spoken(actions)) and results == []

    run(run_external_command(backend, {"device": "Janus Lock", "capability": "lock", "command": "unlock"}))
    assert mock.commands == [("lock-1", "lock", "unlock", [])]

    h.send(3_100_000, [chunk_event("yes please")])
    actions += [er.action for er in h.send(3_150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 7_000_000, stop_on_final=False)
    assert [(n, o.get("already")) for n, o in results] == [("st_unlock", True)]
    assert len(mock.commands) == 1  # only "someone else"'s command; Janus sent nothing
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
