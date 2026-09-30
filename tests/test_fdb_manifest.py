"""T1 (docs/fdb_v3_implementation_plan.md §7): manifest introspection.

Doesn't depend on a local Full-Duplex-Bench checkout being present -- the
fixture below is a minimal source snippet using the exact same shape as
FDB's real `v3/lk_agent_tool.py.AssistantFnc` (decorator description,
`Args:` docstring block, typed params with defaults) so the introspector
is proven against that shape even when FDB isn't checked out locally.
`test_real_fdb_checkout_if_present` additionally runs it against the
real file when `FDB_V3_ROOT` points at one, for an extra, non-blocking
sanity check.
"""

from __future__ import annotations

import os

from conftest import manifest_event

from prism_rt.adapters.fdb_manifest import introspect_source, to_catalog_manifest
from prism_rt.config import Config
from prism_rt.sim.harness import SimHarness

_FIXTURE_SOURCE = '''
def ai_callable_decorator(description=""):
    def wrap(fn):
        fn.__fdb_description__ = description
        return fn
    return wrap


class AssistantFnc:
    @ai_callable_decorator(description="Search for available flights to a destination.")
    async def search_flights(self, destination: str, date: str):
        """
        Args:
            destination: The city or airport, e.g. 'London' or 'LHR'
            date: The travel date, e.g. '2026-08-20'
        """
        pass

    @ai_callable_decorator(description="Calculate commute duration.")
    async def calculate_commute(self, origin_address: str, destination_address: str, mode: str = "driving"):
        """
        Args:
            origin_address: Starting location
            destination_address: Destination location
            mode: Transport mode, defaults to 'driving'
        """
        pass

    @ai_callable_decorator(description="Search products.")
    async def search_products(self, query: str, max_price: float = None):
        """
        Args:
            query: Product search term
            max_price: Optional maximum budget
        """
        pass

    def not_a_tool(self):
        """This has no ai_callable_decorator and must be skipped."""
        pass
'''


def test_introspect_source_finds_only_decorated_methods():
    tools = introspect_source(_FIXTURE_SOURCE)
    names = {t.name for t in tools}
    assert names == {"search_flights", "calculate_commute", "search_products"}


def test_required_vs_optional_params():
    tools = {t.name: t for t in introspect_source(_FIXTURE_SOURCE)}
    sf = tools["search_flights"].params_schema
    assert sf["required"] == ["destination", "date"]
    assert sf["properties"]["destination"]["type"] == "string"
    assert sf["properties"]["destination"]["description"].startswith("The city or airport")

    cc = tools["calculate_commute"].params_schema
    assert cc["required"] == ["origin_address", "destination_address"]
    assert "mode" not in cc["required"]
    assert cc["properties"]["mode"]["default"] == "driving"

    sp = tools["search_products"].params_schema
    assert sp["required"] == ["query"]
    assert "max_price" not in sp["required"]
    assert "default" not in sp["properties"]["max_price"]  # default is None, not recorded


def test_descriptions_carried_through():
    tools = {t.name: t for t in introspect_source(_FIXTURE_SOURCE)}
    assert tools["search_flights"].description == "Search for available flights to a destination."


def test_catalog_accepts_the_generated_manifest_with_no_quarantine():
    tools = introspect_source(_FIXTURE_SOURCE)
    manifest = to_catalog_manifest(tools)
    h = SimHarness(Config(), seed=1)
    h.send(1000, [manifest_event(manifest)])
    usable = {t.name for t in h.store.catalog.usable_tools()}
    assert usable == {"search_flights", "calculate_commute", "search_products"}
    # No mutability declared by FDB -> the catalog's own safe default (W5).
    for t in h.store.catalog.usable_tools():
        assert t.mutability_source == "DEFAULTED"


def test_real_fdb_checkout_if_present():
    root = os.environ.get("FDB_V3_ROOT", os.path.expanduser("~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3"))
    path = os.path.join(root, "lk_agent_tool.py")
    if not os.path.isfile(path):
        return  # no local FDB checkout -- not a failure, just nothing extra to check here
    from prism_rt.adapters.fdb_manifest import introspect_file

    tools = introspect_file(path)
    assert len(tools) == 12
    names = {t.name for t in tools}
    assert names == {
        "search_flights", "book_flight", "update_identity_doc", "get_card_benefits",
        "get_exchange_rate", "modify_autopay", "search_apartments", "calculate_commute",
        "update_search_filter", "track_order", "search_products", "add_to_cart",
    }
