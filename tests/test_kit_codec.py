"""KitCodec: the evaluation kit's wire format <-> Janus envelopes/actions.

Unit-level: every kit event type decodes to the envelopes the kernel
expects, the kit's tool manifest parses cleanly into ToolCatalog, and every
encoded action passes the kit's own `harness/protocol.py.validate_action`.
"""

from __future__ import annotations

from harness.mock_env import TOOL_REGISTRY
from harness.protocol import validate_action

from prism_rt.adapters.kit_codec import KitCodec, manifest_tools
from prism_rt.kernel.ordering import order_batch
from prism_rt.model.actions import Action, CancelBody, FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.types import EMPTY_READ_SET, ActionType, Snapshot, ToolMutability
from prism_rt.store.catalog import ToolCatalog

CODEC = KitCodec()


class _Guard:
    def check(self) -> None:
        pass


def _types(envs) -> list[str]:
    return [e.payload_type for e in envs]


def test_manifest_parses_every_public_tool_with_correct_mutability():
    raw = {"timestamp_ms": 0, "event_type": "tool_manifest", "payload": {"schema_version": "1.0", "tools": dict(TOOL_REGISTRY)}}
    [env] = CODEC.decode(raw, seq=1)
    assert env.payload_type == "manifest" and env.ts_us == 0

    catalog = ToolCatalog(_Guard())
    result = catalog.parse_manifest(env.payload.tools)
    assert result.quarantined == ()
    assert sorted(result.accepted) == sorted(TOOL_REGISTRY)
    for name, spec in TOOL_REGISTRY.items():
        expected = ToolMutability.READ_ONLY if spec["kind"] == "read_only" else ToolMutability.STATE_CHANGING
        assert catalog.get(name).mutability == expected
        assert catalog.get(name).mutability_source == "DECLARED"


def test_manifest_schema_conversion_required_enum_array_nested():
    tools = {t["name"]: t for t in manifest_tools(dict(TOOL_REGISTRY))}
    assert tools["flight_search"]["parameters"]["required"] == ["destination"]
    lookup = tools["lookup_manual"]["parameters"]
    assert lookup["required"] == ["query"]
    assert lookup["properties"]["image_embedding"] == {
        "type": "array",
        "items": {"type": "number"},
        "description": TOOL_REGISTRY["lookup_manual"]["args"]["image_embedding"]["description"],
    }
    assert lookup["properties"]["device_model"]["enum"] == ["QN90", "S24", "WF45", "GENERIC"]
    ticket = tools["create_support_ticket"]["parameters"]
    assert ticket["required"] == ["device", "issue"]
    assert ticket["properties"]["issue"]["required"] == ["summary", "severity"]
    assert ticket["properties"]["issue"]["properties"]["severity"]["enum"] == ["low", "medium", "high"]
    assert ticket["properties"]["device"]["required"] == ["model"]


def test_manifest_unknown_kind_falls_back_to_safe_default():
    catalog = ToolCatalog(_Guard())
    catalog.parse_manifest(manifest_tools({"mystery": {"kind": "sometimes", "args": {"x": {"type": "string", "required": True}}}}))
    assert catalog.get("mystery").mutability == ToolMutability.STATE_CHANGING
    assert catalog.get("mystery").mutability_source == "DEFAULTED"


def test_speech_chunk_without_and_with_end_of_turn():
    mid = CODEC.decode({"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": {"text": "Book a flight to ", "end_of_turn": False}}, seq=2)
    assert _types(mid) == ["text_chunk"] and mid[0].payload.text == "Book a flight to " and mid[0].ts_us == 100_000
    end = CODEC.decode({"timestamp_ms": 800, "event_type": "user_speech_chunk", "payload": {"text": "Oslo.", "end_of_turn": True}}, seq=3)
    assert _types(order_batch(end)) == ["text_chunk", "end_of_turn"]
    assert len({e.event_id for e in end}) == 2


def test_interruption_is_a_complete_barge_in_turn():
    envs = CODEC.decode({"timestamp_ms": 1900, "event_type": "interruption", "payload": {"text": "Wait, make it Lima."}}, seq=4)
    assert _types(order_batch(envs)) == ["interruption", "text_chunk", "end_of_turn"]
    assert envs[1].payload.text == "Wait, make it Lima."
    assert all(e.ts_us == 1_900_000 for e in envs)


def test_interruption_without_text_still_closes_its_turn():
    envs = CODEC.decode({"timestamp_ms": 1900, "event_type": "interruption", "payload": {}}, seq=4)
    assert _types(order_batch(envs)) == ["interruption", "end_of_turn"]


def test_audio_and_frame_events():
    audio = CODEC.decode({"timestamp_ms": 100, "event_type": "user_audio_chunk", "payload": {"audio_ref": "audio/x.mp3", "duration_ms": 900, "end_of_turn": True}}, seq=5)
    assert _types(order_batch(audio)) == ["audio_clip", "end_of_turn"] and audio[0].payload.clip_id == "audio/x.mp3"
    frame = CODEC.decode({"timestamp_ms": 100, "event_type": "video_frame", "payload": {"frame_id": "f_1", "image_ref": "frames/x.png"}}, seq=6)
    assert _types(frame) == ["video_frame"] and frame[0].payload.frame_id == "f_1"


def test_tool_result_success_and_error_status_mapping():
    ok = CODEC.decode(
        {"timestamp_ms": 3100.4, "event_type": "tool_result", "payload": {"call_id": "c-0001", "api_name": "flight_search", "status": "success", "result": {"status": "success", "flights": []}}},
        seq=7,
    )
    assert ok[0].payload.status == "ok" and ok[0].payload.result == {"status": "success", "flights": []}
    assert ok[0].ts_us == 3_100_400
    err = CODEC.decode(
        {"timestamp_ms": 3100, "event_type": "tool_result", "payload": {"call_id": "c-0001", "api_name": "flight_search", "status": "error", "result": {"status": "error", "error": "timeout", "detail": "upstream timed out"}}},
        seq=8,
    )
    assert err[0].payload.status == "error"
    assert err[0].payload.error == {"code": "timeout", "message": "upstream timed out"}
    assert "retryable" not in err[0].payload.error  # the kit never says; nothing is invented


def test_unrecognised_or_malformed_events_are_dropped_not_raised():
    assert CODEC.decode({"timestamp_ms": 5000, "event_type": "scenario_end", "payload": {}}, seq=9) == []
    assert CODEC.decode({"timestamp_ms": 5000, "event_type": "who_knows", "payload": {}}, seq=9) == []
    assert CODEC.decode({"event_type": "user_speech_chunk", "payload": {"text": "hi"}}, seq=9) == []  # no timestamp
    assert CODEC.decode({"timestamp_ms": 1, "event_type": "user_speech_chunk", "payload": {"text": 5}}, seq=9) == []
    assert CODEC.decode({"timestamp_ms": 1, "event_type": "tool_result", "payload": {"call_id": "c", "status": "weird"}}, seq=9) == []
    assert CODEC.decode("not a dict", seq=9) == []


def _action(action_type, body, snapshot=None) -> Action:
    return Action(action_type=action_type, action_id="a-1", ts_us=1, body=body, read_set=EMPTY_READ_SET, snapshot=snapshot)


def test_every_encoded_action_is_valid_per_the_kit_protocol():
    snap = Snapshot(intent="book_flight", slots={"destination": "Lima"}, revision=3, digest="d")
    encoded = [
        CODEC.encode(_action(ActionType.SPEAK, SpeakBody(text="Got it.", kind="ack"))),
        CODEC.encode(_action(ActionType.CLARIFY, SpeakBody(text="Which city?", kind="clarify"))),
        CODEC.encode(_action(ActionType.TOOL_CALL, ToolCallBody(call_id="c-0001", tool_name="flight_search", arguments={"destination": "Lima"}))),
        CODEC.encode(_action(ActionType.CANCEL, CancelBody(target_call_id="c-0001", reason="read_set_invalidated"))),
        CODEC.encode(_action(ActionType.FINAL, FinalBody(text="Found flights."), snapshot=snap)),
    ]
    assert [e["action"] for e in encoded] == ["filler_speech", "clarification_request", "tool_call", "cancel_tool", "final_response"]
    for e in encoded:
        assert validate_action(e) == [], e
    assert encoded[2]["payload"] == {"call_id": "c-0001", "api_name": "flight_search", "args": {"destination": "Lima"}}
    assert encoded[3]["payload"] == {"call_id": "c-0001"}
    assert encoded[4]["state_snapshot"] == {"intent": "book_flight", "slots": {"destination": "Lima"}}
    assert "state_snapshot" not in encoded[0]


def test_timestamp_extraction():
    assert CODEC.timestamp_us({"timestamp_ms": 12.5}) == 12_500
    assert CODEC.timestamp_us({"ts_us": 5}) is None
    assert CODEC.timestamp_us({"timestamp_ms": "x"}) is None
