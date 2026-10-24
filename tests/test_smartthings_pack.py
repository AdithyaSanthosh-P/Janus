"""WP3: the SmartThings tool pack against the mock backend and a fake HTTP layer."""

from __future__ import annotations

import asyncio
import json
import urllib.error

import pytest
from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.client import SmartThingsClient, SmartThingsError
from prism_rt.smartthings.confirm import confirm_state
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.store.catalog import ToolCatalog
from prism_rt.workers.gateway import ScriptedProvider


async def _no_sleep(_s):
    return None


def run(coro):
    return asyncio.run(coro)


def toolset(mock=None):
    mock = mock or MockSmartThings()
    return mock, SmartThingsToolset(mock, sleep=_no_sleep)


def call(ts, name, **args):
    return run(ts.make_executor()(name, args))


# ---- manifest ---------------------------------------------------------------

def test_manifest_parses_and_flags_security_writes():
    from prism_rt.store.facts import MutationGuard

    guard = MutationGuard()
    guard.active = True
    cat = ToolCatalog(guard)
    res = cat.parse_manifest(MANIFEST)
    assert res.quarantined == ()
    assert cat.get("st_unlock").requires_confirmation and cat.get("st_open_garage").requires_confirmation
    assert not cat.get("st_lock").requires_confirmation
    assert all(cat.get(n).repeatable for n in ("st_lock", "st_unlock", "st_switch", "st_set_cooling_setpoint"))


# ---- tools on the mock ------------------------------------------------------

def test_lock_sends_one_command_and_reads_it_back():
    mock, ts = toolset()
    out = call(ts, "st_lock", device="front door")
    assert out["done"] is True and out["state"] == "locked" and not out.get("already")
    assert [c[1:] for c in mock.commands] == [("lock", "lock", [])]


def test_already_locked_sends_nothing():
    mock, ts = toolset()
    call(ts, "st_lock", device="front door")
    mock.commands.clear()
    out = call(ts, "st_lock", device="front door")
    assert out["done"] is True and out["already"] is True and mock.commands == []


def test_accepted_but_never_changes_is_reported_unconfirmed():
    mock, ts = toolset(MockSmartThings(stuck={"lock"}))
    out = call(ts, "st_lock", device="front door")
    assert out["outcome"] == "unconfirmed" and out["sent"] is True and "done" not in out
    assert len(mock.commands) == 1


def test_setpoint_range_is_enforced_and_set():
    mock, ts = toolset()
    assert call(ts, "st_set_cooling_setpoint", device="ac", celsius=26)["state"] == 26
    with pytest.raises(SmartThingsError, match="16"):
        call(ts, "st_set_cooling_setpoint", device="ac", celsius=60)
    assert len(mock.commands) == 1


def test_switch_garage_mode_and_scene():
    mock, ts = toolset()
    assert call(ts, "st_switch", device="light", state="on")["state"] == "on"
    assert call(ts, "st_open_garage", device="garage")["state"] == "open"
    assert call(ts, "st_close_garage", device="garage")["state"] == "closed"
    assert call(ts, "st_set_location_mode", mode="Night")["state"] == "Night"
    assert call(ts, "st_execute_scene", scene="Goodnight")["done"] is True


def test_ambiguous_device_is_an_honest_error_not_a_guess():
    mock, ts = toolset(MockSmartThings(extra_lock=True))
    with pytest.raises(SmartThingsError, match="which device"):
        call(ts, "st_lock", device="sauna")
    assert mock.commands == []
    assert call(ts, "st_lock", device="back door")["device"] == "Back Door Lock"


def test_a_place_the_home_does_not_have_is_not_guessed_as_the_only_device():
    """One lock at home: "front door", "the door" and "lock" are it; "back door" is not."""
    mock, ts = toolset(MockSmartThings())
    for spoken in ("front door", "the door", "lock", "my front door"):
        assert call(ts, "st_lock", device=spoken)["device"] == "Janus Lock"
    mock.commands.clear()
    for spoken in ("back door", "the side door", "kitchen door"):
        with pytest.raises(SmartThingsError, match="only door lock I can see is called Janus Lock"):
            call(ts, "st_unlock", device=spoken)
    assert mock.commands == []
    with pytest.raises(SmartThingsError, match="can't find"):
        run(ts.read_target("st_unlock", "back door"))
    # A lamp is not a door: "the living room light" is still the one switch.
    assert call(ts, "st_switch", device="living room light", state="off")["device"] == "Janus Switch"


def test_garage_capability_is_detected_from_the_device():
    mock = MockSmartThings(garage_capability="doorControl")
    _, ts = toolset(mock)
    assert call(ts, "st_open_garage", device="garage")["state"] == "open"
    assert mock.commands[-1][1] == "doorControl"


def test_read_tools():
    _, ts = toolset()
    assert {d["label"] for d in call(ts, "st_list_devices")["devices"]} >= {"Janus Lock", "Janus Garage"}
    st = call(ts, "st_get_status", device="front door")
    assert st["found"] is True and st["state"] == "unlocked"


# ---- confirm_state ----------------------------------------------------------

def test_confirm_state_reads_at_most_four_times():
    mock = MockSmartThings(stuck={"lock"})
    ok = run(confirm_state(mock, "lock-1", "lock", "lock", "locked", sleep=_no_sleep))
    assert ok is False and mock.status_reads <= 4


# ---- client HTTP shapes -----------------------------------------------------

class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, dict(headers), body))
        status, payload = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "x", {}, __import__("io").BytesIO(json.dumps(payload).encode()))
        return status, {"X-RateLimit-Remaining": "99"}, json.dumps(payload).encode()


def client(responses, now=lambda: 0.0):
    http = FakeHttp(responses)
    return http, SmartThingsClient("tok", http=http, clock=now)


def test_client_command_shape_and_auth():
    http, c = client([(200, {"results": [{"status": "ACCEPTED"}]})])
    out = run(c.command("dev-1", "lock", "lock", []))
    method, url, headers, body = http.requests[0]
    assert (method, url) == ("POST", "https://api.smartthings.com/v1/devices/dev-1/commands")
    assert headers["Authorization"] == "Bearer tok"
    assert json.loads(body) == {"commands": [{"component": "main", "capability": "lock", "command": "lock", "arguments": []}]}
    assert out["results"][0]["status"] == "ACCEPTED"


@pytest.mark.parametrize("code,word", [(401, "expired"), (403, "scope"), (422, "rejected"), (429, "rate")])
def test_client_maps_http_errors_to_honest_messages(code, word):
    _, c = client([(code, {"error": {"message": "m"}})])
    with pytest.raises(SmartThingsError) as exc:
        run(c.status("dev-1"))
    assert word in str(exc.value).lower()


def test_client_rate_budget_refuses_the_13th_command_in_a_minute():
    http, c = client([(200, {})] * 12)
    for _ in range(12):
        run(c.command("dev-1", "switch", "on"))
    with pytest.raises(SmartThingsError) as exc:
        run(c.command("dev-1", "switch", "on"))
    assert "rate" in str(exc.value).lower() and len(http.requests) == 12


def test_client_read_budget_refuses_the_61st_status_read_in_a_minute():
    http, c = client([(200, {"components": {}})] * 60)
    for _ in range(60):
        run(c.status("dev-1"))
    with pytest.raises(SmartThingsError, match="rate limit"):
        run(c.status("dev-1"))
    assert len(http.requests) == 60


def test_client_set_mode_shape():
    http, c = client([(200, {"id": "m1"})])
    run(c.set_mode("loc-1", "m1"))
    method, url, _h, body = http.requests[0]
    assert (method, url) == ("PUT", "https://api.smartthings.com/v1/locations/loc-1/modes/current")
    assert json.loads(body) == {"modeId": "m1"}


# ---- end to end through the kernel -------------------------------------------

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}


def demo(**kw):
    base = dict(action_plans_enabled=True, multi_action_enabled=True, conversational_replies_enabled=True,
                confirmation_policy_enabled=True, unconfirmed_outcomes_enabled=True,
                settle_barrier_enabled=True, settle_ms=500)
    base.update(kw)
    return Config(**base)


def e2e(interpret, utterance, mock=None):
    mock, ts = toolset(mock)
    provider = ScriptedProvider()
    for key, reply in interpret:
        provider.register("interpret", key, reply)
    provider.register("compose", "a0", {"text": "Done.", "claims": []})
    executor = ts.make_executor()
    sent = []

    def handler_for(name):
        def handler(args):
            sent.append((name, dict(args)))
            return run(executor(name, args))
        return handler

    tools = {m["name"]: {"latency_ms": 200, "handler": handler_for(m["name"])} for m in MANIFEST}
    h = SimHarness(demo(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    h.send(100_000, [chunk_event(utterance)])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 4_000_000, stop_on_final=False)
    return h, mock, sent, actions


def test_lock_then_setpoint_correction_sends_only_the_corrected_value():
    goal = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True, "actions": [
        {"tool": "st_lock", "args": {"device": "front door"}},
        {"tool": "st_set_cooling_setpoint", "args": {"device": "ac", "celsius": 26}}]}
    h, mock, sent, _ = e2e([("lock the door", goal)], "lock the door and set the ac to twenty six")
    assert sorted(n for n, _ in sent) == ["st_lock", "st_set_cooling_setpoint"]
    assert [a["celsius"] for n, a in sent if n == "st_set_cooling_setpoint"] == [26]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_unlock_asks_before_sending_anything():
    goal = {"act": "new_goal", "intent": "st_unlock", "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": "st_unlock", "args": {"device": "front door"}}]}
    h, mock, sent, actions = e2e([("unlock the door", goal)], "unlock the door")
    assert sent == [] and mock.commands == []
    assert any("security action" in a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY))


# ---- factory ------------------------------------------------------------------

def test_factory_defaults_to_mock_and_real_needs_a_token():
    from prism_rt.smartthings.factory import make_backend

    assert isinstance(make_backend({}), MockSmartThings)
    with pytest.raises(SystemExit):
        make_backend({"JANUS_ST_MODE": "real"})
    assert isinstance(make_backend({"JANUS_ST_MODE": "real", "ST_PAT": "t"}), SmartThingsClient)
    with pytest.raises(SystemExit):
        make_backend({"JANUS_ST_MODE": "banana"})


def test_combined_toolset_dispatches_by_tool_name():
    from prism_rt.devicecare.tools import DeviceCareToolset
    from prism_rt.smartthings.factory import combine

    manifest, execute = combine(DeviceCareToolset(), SmartThingsToolset(MockSmartThings(), sleep=_no_sleep))
    names = [t["name"] for t in manifest]
    assert len(names) == len(set(names)) and "st_lock" in names and "identify_indicator" in names
    assert run(execute("st_lock", {"device": "front door"}))["done"] is True
    with pytest.raises(ValueError):
        run(execute("nope", {}))


def test_scene_tool_with_no_scenes_says_so():
    class NoScenes(MockSmartThings):
        async def list_scenes(self, location_id):
            return []

    _, ts = toolset(NoScenes())
    with pytest.raises(SmartThingsError, match="no saved scenes"):
        call(ts, "st_execute_scene", scene="Goodnight")


# ---- device-state callback (the app feed) ---------------------------------------

def test_device_state_callback_reports_confirmed_and_already_and_reads():
    seen = []
    mock = MockSmartThings()
    ts = SmartThingsToolset(mock, sleep=_no_sleep, on_device_state=lambda *a: seen.append(a))
    run(ts.make_executor()("st_lock", {"device": "front door"}))
    run(ts.make_executor()("st_lock", {"device": "front door"}))  # already
    run(ts.make_executor()("st_get_status", {"device": "front door"}))
    assert seen == [("lock-1", "Janus Lock", "lock", "lock", "locked")] * 3


def test_unconfirmed_write_reports_the_state_actually_read_back():
    seen = []
    ts = SmartThingsToolset(MockSmartThings(stuck={"lock"}), sleep=_no_sleep, on_device_state=lambda *a: seen.append(a))
    run(ts.make_executor()("st_lock", {"device": "front door"}))
    assert seen[-1][-1] == "unlocked"


def test_snapshot_reports_every_device_once():
    seen = []
    ts = SmartThingsToolset(MockSmartThings(), sleep=_no_sleep, on_device_state=lambda *a: seen.append(a))
    run(ts.snapshot_states())
    assert {s[1] for s in seen} == {"Janus Lock", "Janus Garage", "Janus Thermostat", "Janus Switch"}
    assert ("thermo-1", "Janus Thermostat", "thermostatCoolingSetpoint", "coolingSetpoint", 24) in seen


def test_a_status_read_carries_only_what_a_person_asks_about():
    """F40/F12 (scripted runs, 24 Oct): every raw attribute reached the reply, and a
    virtual device's odd ones were read aloud ("ondol mode", "4316.7 degrees", "its
    contact sensor is reading as open"). Now: the device's own state, a lock's battery,
    a plausible room temperature."""
    mock, ts = toolset()
    thermostat = call(ts, "st_get_status", device="thermostat")
    assert set(thermostat["attributes"]) <= {"coolingSetpoint", "room_temperature"}
    assert thermostat["attributes"].get("room_temperature") == 29
    lock = call(ts, "st_get_status", device="front door")
    assert set(lock["attributes"]) <= {"lock", "battery"}

    real_status = mock.status

    async def odd_status(device_id):
        status = await real_status(device_id)
        main = status["components"]["main"]
        main.setdefault("temperatureMeasurement", {})["temperature"] = {"value": 4316.7}
        main["thermostatMode"] = {"thermostatMode": {"value": "ondol"}}
        main["contactSensor"] = {"contact": {"value": "open"}}
        return status

    mock.status = odd_status
    thermostat = call(ts, "st_get_status", device="thermostat")
    assert "room_temperature" not in thermostat["attributes"] and "thermostatMode" not in str(thermostat)
    garage = call(ts, "st_get_status", device="garage")
    assert "contact" not in str(garage["attributes"])


def test_two_degrees_cooler_changes_the_current_setting():
    """F39 (scripted run, 24 Oct): "Make it two degrees cooler" was refused."""
    mock, ts = toolset()
    before = run(ts.read_target("st_set_cooling_setpoint", "thermostat"))
    result = call(ts, "st_set_cooling_setpoint", device="thermostat", change_by=-2)
    assert result["done"] is True and float(result["state"]) == float(before) - 2
    with pytest.raises(SmartThingsError, match="between 16 and 30"):
        call(ts, "st_set_cooling_setpoint", device="thermostat", change_by=-40)


def test_status_reads_do_not_use_up_the_command_budget():
    """F42 (scripted run, 24 Oct): the 4th switch command within a minute was refused,
    because status reads (before and after each command, and the evidence watch) shared
    the 12-a-minute command budget."""
    clock = [0.0]

    def http(method, url, headers, body):
        return 200, {}, b"{}"

    client = SmartThingsClient("token", http=http, clock=lambda: clock[0])
    for _ in range(40):
        run(client.status("dev-1"))
    for _ in range(12):
        run(client.command("dev-1", "switch", "on"))
    with pytest.raises(SmartThingsError, match="rate limit"):
        run(client.command("dev-1", "switch", "on"))
    clock[0] = 61.0
    run(client.command("dev-1", "switch", "on"))
