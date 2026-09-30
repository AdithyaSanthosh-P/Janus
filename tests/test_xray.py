"""The kernel X-ray renders invalidated work struck through and collapses holds."""

from __future__ import annotations

from prism_rt.observability.xray import build_rows, render

WIRE = [
    {"dir": "in", "t_us": 0, "type": "text_chunk", "payload": {"text": "orange light"}},
    {"dir": "out", "t_us": 300_000, "action_type": "tool_call", "body": {"tool_name": "lookup", "arguments": {"c": "orange"}, "call_id": "c-1"}},
    {"dir": "in", "t_us": 900_000, "type": "text_chunk", "payload": {"text": "no, red"}},
    {"dir": "out", "t_us": 950_000, "action_type": "final", "body": {"text": "Done."}},
]
DECISIONS = [
    {"now_us": 100_000, "fact_changes": [], "invalidations": {}, "rejected": [{"action_type": "tool_call", "reason": "G10_settle"}]},
    {"now_us": 200_000, "fact_changes": [], "invalidations": {}, "rejected": [{"action_type": "tool_call", "reason": "G10_settle"}]},
    {"now_us": 920_000, "fact_changes": [], "rejected": [],
     "invalidations": {"changed_keys": ["slot.g1.colour"], "invalidated_call_ids": ["c-1"], "discarded_call_ids": []}},
]


def test_invalidated_call_is_struck_and_repeated_holds_collapse_to_one_row():
    rows = build_rows(DECISIONS, WIRE)
    call = next(r for r in rows if r["lane"] == "TOOLS")
    assert call.get("struck") is True and call["tag"] == "invalidated"
    holds = [r for r in rows if r["cls"] == "hold"]
    assert len(holds) == 1 and "settling" in holds[0]["text"] and "(0.1s)" in holds[0]["text"]
    assert any(r["cls"] == "invalidate" for r in rows)


def test_render_is_self_contained_html():
    page = render(build_rows(DECISIONS, WIRE), title="t")
    assert page.startswith("<!doctype html>") and "<script" not in page and "http" not in page.split("</style>")[1]
    assert "struck" in page and "Done." in page
