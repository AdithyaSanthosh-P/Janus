"""T1 (docs/fdb_v3_implementation_plan.md §7): tool adapter execution +
telemetry-line byte-compatibility with FDB's own parser.

`test_against_real_fdb_registry_if_present` additionally runs the
adapter against FDB's real, unmodified `mock_apis.MockAPIRegistry` when
`FDB_V3_ROOT` points at a local checkout -- a fake stand-in registry
covers the same behavior without that dependency so the suite stays
green without a local FDB checkout.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import pytest

from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter, fill_schema_defaults


class FakeRegistry:
    """Same call surface as FDB's `mock_apis.MockAPIRegistry`."""

    def __init__(self):
        self.received: list[tuple[str, dict]] = []

    def call(self, function_name: str, **kwargs) -> dict:
        self.received.append((function_name, dict(kwargs)))
        if function_name == "add_to_cart":
            return {"status": "success", "product_id": kwargs["product_id"], "quantity": kwargs["quantity"]}
        return {"status": "success"}


ADD_TO_CART_SCHEMA = {
    "type": "object",
    "properties": {
        "product_id": {"type": "string"},
        "quantity": {"type": "integer", "default": 1},
    },
    "required": ["product_id"],
}


def test_fill_schema_defaults_only_fills_whats_missing():
    filled = fill_schema_defaults(ADD_TO_CART_SCHEMA, {"product_id": "PROD1"})
    assert filled == {"product_id": "PROD1", "quantity": 1}

    # A value the model actually gave is never overridden, even if it
    # equals the schema default coincidentally or not.
    filled2 = fill_schema_defaults(ADD_TO_CART_SCHEMA, {"product_id": "PROD1", "quantity": 3})
    assert filled2 == {"product_id": "PROD1", "quantity": 3}


def test_execute_fills_defaults_calls_registry_and_logs_fdb_shape():
    registry = FakeRegistry()
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "agent_tool_calls.log")
        adapter = FdbToolAdapter(
            registry, room_name="eval-abc123", log_path=log_path,
            get_params_schema=lambda name: ADD_TO_CART_SCHEMA if name == "add_to_cart" else None,
        )
        result = asyncio.run(adapter.execute("add_to_cart", {"product_id": "PROD1"}))
        assert result == {"status": "success", "product_id": "PROD1", "quantity": 1}
        assert registry.received == [("add_to_cart", {"product_id": "PROD1", "quantity": 1})]

        with open(log_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        line = lines[0]
        assert line["room"] == "eval-abc123"
        assert line["call"]["function"] == "add_to_cart"
        assert line["call"]["args"] == {"product_id": "PROD1", "quantity": 1}
        assert isinstance(line["call"]["timestamp_start"], float)
        assert isinstance(line["call"]["timestamp_end"], float)
        assert line["call"]["timestamp_start"] <= line["call"]["timestamp_end"]


def test_no_schema_lookup_sends_args_unchanged():
    registry = FakeRegistry()
    with tempfile.TemporaryDirectory() as d:
        adapter = FdbToolAdapter(registry, room_name="r1", log_path=os.path.join(d, "log.jsonl"))
        asyncio.run(adapter.execute("track_order", {"order_id": "BOB12"}))
        assert registry.received == [("track_order", {"order_id": "BOB12"})]


def test_against_real_fdb_registry_if_present():
    root = os.environ.get("FDB_V3_ROOT", os.path.expanduser("~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3"))
    if not os.path.isfile(os.path.join(root, "mock_apis.py")):
        return
    sys.path.insert(0, root)
    try:
        import mock_apis

        registry = mock_apis.MockAPIRegistry(latency_profile="instant", enable_logging=False)
    finally:
        sys.path.remove(root)

    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "agent_tool_calls.log")
        adapter = FdbToolAdapter(
            registry, room_name="eval-real",
            log_path=log_path,
            get_params_schema=lambda name: {"properties": {"quantity": {"default": 1}}} if name == "add_to_cart" else None,
        )
        result = asyncio.run(adapter.execute("add_to_cart", {"product_id": "PROD1"}))
        assert result["status"] == "success"
        with open(log_path, "r", encoding="utf-8") as f:
            line = json.loads(f.readline())
        assert line["call"]["args"]["quantity"] == 1
