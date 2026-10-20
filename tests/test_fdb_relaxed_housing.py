"""WP5: Config.fdb_relaxed_housing_schema -- the relaxed housing tool contract.

`search_apartments` asks only for the city (bedrooms and budget optional, a
`pets_allowed` flag added) and `update_search_filter.value` takes a number, a
boolean or a string. The backend still gets the mock's required arguments
(neutral fills), but the logged call carries only what the user said, and no
result field computed from a fill is ever shown to the agent. Every other tool
is untouched, so only the housing domain can change.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path

import pytest
from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.adapters import fdb_schema_overrides as overrides
from prism_rt.adapters.fdb_schema_overrides import BACKEND_FILLS, apply_schema_overrides, maybe_apply
from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter
from prism_rt.kernel.action_plans import coerce_to_schema
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.store.catalog import ToolCatalog, value_matches_schema_type
from prism_rt.store.facts import MutationGuard
from prism_rt.workers.gateway import ScriptedProvider

# The published FDB-v3 manifest as Janus introspects it (three of its tools).
PUBLISHED = [
    {"name": "search_apartments", "description": "Search for available rental apartments.", "params_schema": {
        "type": "object", "properties": {
            "city": {"type": "string", "description": "Destination city"},
            "bedrooms": {"type": "integer", "description": "Number of bedrooms"},
            "max_price": {"type": "number", "description": "Maximum monthly rent budget"}},
        "required": ["city", "bedrooms", "max_price"]}},
    {"name": "update_search_filter", "description": "Instantly update the user's search filter.", "params_schema": {
        "type": "object", "properties": {
            "filter_name": {"type": "string", "description": "Filter key to modify"},
            "value": {"type": "string", "description": "Filter value to apply"}},
        "required": ["filter_name", "value"]}},
    {"name": "calculate_commute", "description": "Commute time.", "params_schema": {
        "type": "object", "properties": {
            "origin_address": {"type": "string"}, "destination_address": {"type": "string"},
            "mode": {"type": "string", "default": "driving"}},
        "required": ["origin_address", "destination_address"]}},
]


def relaxed():
    return {t["name"]: t for t in apply_schema_overrides(copy.deepcopy(PUBLISHED))}


# ---- the manifest -----------------------------------------------------------

def test_search_apartments_needs_only_the_city_and_gains_pets_allowed():
    schema = relaxed()["search_apartments"]["params_schema"]
    assert schema["required"] == ["city"]
    assert schema["properties"]["pets_allowed"]["type"] == "boolean"
    assert set(schema["properties"]) == {"city", "bedrooms", "max_price", "pets_allowed"}


def test_filter_value_takes_a_number_a_boolean_or_a_string():
    value = relaxed()["update_search_filter"]["params_schema"]["properties"]["value"]
    assert value["type"] == ["number", "boolean", "string"]


def test_no_default_keys_are_introduced():
    for name in ("search_apartments", "update_search_filter"):
        assert "default" not in json.dumps(relaxed()[name]["params_schema"])


def test_other_tools_are_untouched_and_the_input_is_not_mutated():
    before = copy.deepcopy(PUBLISHED)
    out = apply_schema_overrides(before)
    assert before == PUBLISHED
    assert [t for t in out if t["name"] == "calculate_commute"] == [PUBLISHED[2]]


def test_flag_off_leaves_the_manifest_identical():
    assert maybe_apply(copy.deepcopy(PUBLISHED), False) == PUBLISHED
    assert maybe_apply(copy.deepcopy(PUBLISHED), True) != PUBLISHED
    assert fdb_v3_config().fdb_relaxed_housing_schema is False


# ---- catalog support for a list of types -------------------------------------

def catalog(manifest):
    guard = MutationGuard()
    guard.active = True
    cat = ToolCatalog(guard)
    assert cat.parse_manifest(manifest).quarantined == ()
    return cat


@pytest.mark.parametrize("value,ok", [(True, True), (3000, True), (2.5, True), ("pet friendly", True), ({"a": 1}, False), ([1], False)])
def test_union_type_validates_any_member(value, ok):
    cat = catalog(list(relaxed().values()))
    assert cat.validate_args("update_search_filter", {"filter_name": "x", "value": value}).valid is ok
    prop = relaxed()["update_search_filter"]["params_schema"]["properties"]["value"]
    assert value_matches_schema_type(prop, value) is ok


def test_apartment_search_with_only_a_city_is_valid():
    cat = catalog(list(relaxed().values()))
    assert cat.validate_args("search_apartments", {"city": "Lisbon", "pets_allowed": True}).valid


# ---- coercion into a union -----------------------------------------------------

UNION = {"type": ["number", "boolean", "string"]}


@pytest.mark.parametrize("raw,want", [
    ("$3,000", 3000), ("2", 2), (4, 4), ("2.5", 2.5),
    ("yes", True), ("true", True), ("allowed", True), ("no", False), ("False", False), (True, True),
    ("pet friendly", "pet friendly"), ("downtown", "downtown"),
])
def test_union_coercion_prefers_number_then_boolean_then_string(raw, want):
    got = coerce_to_schema(raw, UNION, "value")
    assert got == want and type(got) is type(want)


def test_single_type_coercion_is_unchanged():
    assert coerce_to_schema("$1,800 a month", {"type": "string"}, "value") == "1800"
    assert coerce_to_schema("yes", {"type": "boolean"}, "x") is True


# ---- the adapter: fills for the backend only ---------------------------------

class Registry:
    def __init__(self):
        self.received = []

    def call(self, name, **kw):
        self.received.append((name, dict(kw)))
        if name == "search_apartments":
            return {"status": "success", "city": kw["city"],
                    "results": [{"id": "APT1", "price": kw["max_price"] - 100, "beds": kw["bedrooms"]}]}
        return {"status": "success"}


def run_adapter(args, fills=BACKEND_FILLS):
    reg = Registry()
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "calls.log")
        adapter = FdbToolAdapter(reg, "room", log_path=log, backend_fills=fills)
        result = asyncio.run(adapter.execute("search_apartments", args))
        logged = json.loads(Path(log).read_text().splitlines()[0])["call"]["args"]
    return reg.received[0][1], logged, result


def test_backend_gets_fills_but_the_log_keeps_only_what_was_said():
    sent, logged, result = run_adapter({"city": "Miami", "max_price": 1500, "pets_allowed": True})
    assert sent["bedrooms"] == BACKEND_FILLS["search_apartments"]["bedrooms"]["value"]
    assert sent["max_price"] == 1500
    assert logged == {"city": "Miami", "max_price": 1500, "pets_allowed": True}


def test_result_fields_computed_from_a_fill_are_removed():
    _sent, _logged, result = run_adapter({"city": "Miami", "max_price": 1500})
    assert result["results"] == [{"id": "APT1", "price": 1400}]  # no made-up bedroom count
    _sent, _logged, result = run_adapter({"city": "Miami", "bedrooms": 2})
    assert result["results"] == [{"id": "APT1", "beds": 2}]  # no made-up price


def test_stated_values_are_never_overridden_by_a_fill():
    sent, logged, _ = run_adapter({"city": "Miami", "bedrooms": 3, "max_price": 2000})
    assert sent == logged == {"city": "Miami", "bedrooms": 3, "max_price": 2000}


def test_no_fills_means_unchanged_behaviour():
    sent, logged, result = run_adapter({"city": "Miami", "bedrooms": 3, "max_price": 2000}, fills=None)
    assert result["results"][0] == {"id": "APT1", "price": 1900, "beds": 3}


# ---- the kernel: an optional parameter is never assumed -----------------------

FAST = {k: 10_000 for k in JobKind}


def _search(cfg, manifest, assumed):
    provider = ScriptedProvider()
    provider.register("interpret", "apartment", {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [], "actions": [
        {"tool": "search_apartments", "args": {"city": "Miami", "bedrooms": 1, "max_price": 1500}, "assumed": assumed}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = SimHarness(cfg, seed=1, provider=provider, tools={"search_apartments": {"latency_ms": 100, "response": {"results": []}}},
                   worker_latency_us=FAST)
    h.send(0, [manifest_event(manifest)])
    h.send(100_000, [chunk_event("a pet friendly apartment in Miami under 1500")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 5_000_000)
    calls = [dict(a.body.arguments) for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return calls


def test_relaxed_schema_drops_an_assumed_optional_bedroom_count():
    calls = _search(fdb_v3_config(fdb_relaxed_housing_schema=True), list(relaxed().values()), ["bedrooms"])
    assert calls == [{"city": "Miami", "max_price": 1500}]


def test_stated_optional_values_are_kept():
    calls = _search(fdb_v3_config(fdb_relaxed_housing_schema=True), list(relaxed().values()), [])
    assert calls == [{"city": "Miami", "bedrooms": 1, "max_price": 1500}]


def test_flag_off_keeps_todays_assumption_of_a_required_value():
    calls = _search(fdb_v3_config(), copy.deepcopy(PUBLISHED), ["bedrooms"])
    assert calls == [{"city": "Miami", "bedrooms": 1, "max_price": 1500}]


# ---- integrity ----------------------------------------------------------------

DATA = Path(os.environ.get("FDB_V3_ROOT", os.path.expanduser("~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3"))) / "benchmark_data_v2.json"


@pytest.mark.skipif(not DATA.is_file(), reason="FDB checkout not present")
def test_no_expected_argument_value_from_the_benchmark_is_written_into_the_overrides():
    """Descriptions stay general: no city, filter name or other labelled value
    from the benchmark's expected calls appears in the override module."""
    import re

    source = Path(overrides.__file__).read_text().lower()
    # The module must name the tools and parameters it declares; those are
    # identifiers of the published/relaxed contract, not leaked labels.
    declared = {t["name"] for t in PUBLISHED} | {p for t in relaxed().values() for p in t["params_schema"]["properties"]}
    data = json.loads(DATA.read_text())
    items = [it for group in (data.values() if isinstance(data, dict) else [data]) for it in (group if isinstance(group, list) else [group])]
    leaked = set()
    for it in items:
        for call in (it.get("expected_tool_calls") or []) if isinstance(it, dict) else []:
            for value in (call.get("args") or {}).values():
                if (isinstance(value, str) and len(value) >= 4 and value not in declared
                        and re.search(rf"(?<![a-z0-9_]){re.escape(value.lower())}(?![a-z0-9_])", source)):
                    leaked.add(value)
    assert leaked == set()
