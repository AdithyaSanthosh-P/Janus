"""KitCodec: the Theme 05 evaluation kit's wire format <-> internal Envelope/Action.

A second codec beside `HarnessCodec`, not a replacement: `HarnessCodec`
keeps Janus's own provisional dialect (every existing test and demo uses
it); this one speaks the dialect of the evaluation kit merged in commit
`7da1fd1` (`docs/PROTOCOL.md`, `harness/protocol.py`, `harness/runner.py`).
That kit was obtained from another team's repository and has not been
verified byte-identical to the organiser's copy, so it is the current local
development target, not a proven official contract.

Like `HarnessCodec`, this is the only place kit wire field names appear
(`docs/prompt 2.txt` §2.1) and it holds no state. It only translates: every
decoded event still enters through `Kernel.step()` as an ordinary Envelope,
and every encoded action is one `EmissionGate` already emitted. Nothing
here touches the store, gates, router or task state.

Inbound mapping (kit `event_type` -> Janus envelopes, all at the event's
own timestamp; `timestamp_ms` -> `ts_us`):

- `tool_manifest`      -> `manifest`. The kit's `{name: {kind, args, ...}}`
  map becomes the catalog's list of `{name, description, parameters,
  mutability}`: per-arg `required: true` becomes a JSON-Schema `required`
  list (recursively for nested `object` args), `items: "number"` becomes
  `{"type": "number"}`, `kind` `read_only`/`state_modifying` becomes
  mutability `read_only`/`state_changing`. An unrecognised `kind` is left
  out so `ToolCatalog` applies its safe default (state_changing, W5).
- `user_speech_chunk`  -> `text_chunk`, plus `end_of_turn` when the kit's
  `end_of_turn` flag is true (the kit folds EOT into the chunk; Janus keeps
  it a separate event class).
- `interruption`       -> `interruption` + `text_chunk(text)` +
  `end_of_turn`. In the kit an interruption is a complete barge-in
  utterance: it carries the correction in `payload.text` and no chunk or
  end-of-turn ever follows it (`docs/PROTOCOL.md` §1.4). In Janus an
  interruption only opens a turn (`TurnManager.on_interruption`); without
  the text and a closing EOT the turn would stay open forever and the floor
  rule (`FastResponder.decide`, CS-28) would keep the agent silent. The
  three envelopes share one `seq`; `EventClass` ordering
  (INTERRUPTION < USER_CONTENT < END_OF_TURN) applies them in that order.
- `user_audio_chunk`   -> `audio_clip(clip_id=audio_ref)`, plus
  `end_of_turn` when flagged.
- `video_frame`        -> `video_frame(frame_id)`.
- `tool_result`        -> `tool_result`: `status` `success` -> `ok`,
  `error` -> `error` with `error = {"code", "message"}` taken from the
  kit's `result.error`/`result.detail`. The kit never states whether an
  error is retryable, so no `retryable` key is invented; Janus's retry
  table applies its own defaults for an absent flag (P0.2).
- `scenario_end`       -> nothing. Janus has no scenario-boundary event;
  the harness cancels `run()` after its tail window.

Outbound mapping (Janus `ActionType` -> kit `action`):

- SPEAK     -> `filler_speech`         `{"text"}`
- CLARIFY   -> `clarification_request` `{"text"}`
- TOOL_CALL -> `tool_call`             `{"call_id", "api_name", "args"}`
- CANCEL    -> `cancel_tool`           `{"call_id"}`
- FINAL     -> `final_response`        `{"text"}`
- any action carrying a Snapshot also gets a top-level
  `state_snapshot: {"intent", "slots"}` (FINAL always carries one).
"""

from __future__ import annotations

from prism_rt.errors import CodecError
from prism_rt.model.actions import Action, CancelBody, FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.events import (
    EVENT_CLASS_BY_PAYLOAD_TYPE,
    AudioClipPayload,
    EndOfTurnPayload,
    Envelope,
    InterruptionPayload,
    ManifestPayload,
    TextChunkPayload,
    ToolResultPayload,
    VideoFramePayload,
)
from prism_rt.model.types import ActionType

_KIND_TO_MUTABILITY = {"read_only": "read_only", "state_modifying": "state_changing"}

_ACTION_NAMES = {
    ActionType.SPEAK: "filler_speech",
    ActionType.CLARIFY: "clarification_request",
    ActionType.TOOL_CALL: "tool_call",
    ActionType.CANCEL: "cancel_tool",
    ActionType.FINAL: "final_response",
}


def _arg_schema(spec: dict) -> dict:
    """One kit arg spec -> a JSON-Schema property. Keeps `type`, `enum`,
    `description`; converts `items` and nested `properties`/`required`."""
    out: dict = {}
    for key in ("type", "enum", "description"):
        if key in spec:
            out[key] = spec[key]
    items = spec.get("items")
    if isinstance(items, str):
        out["items"] = {"type": items}
    elif isinstance(items, dict):
        out["items"] = _arg_schema(items)
    props = spec.get("properties")
    if isinstance(props, dict):
        out["properties"] = {n: _arg_schema(p) for n, p in props.items() if isinstance(p, dict)}
        required = [n for n, p in props.items() if isinstance(p, dict) and p.get("required") is True]
        if required:
            out["required"] = required
    return out


def manifest_tools(tools: dict) -> list[dict]:
    """The kit's `tool_manifest.payload.tools` map -> ToolCatalog's list form."""
    out: list[dict] = []
    for name, spec in tools.items():
        if not isinstance(spec, dict):
            out.append({"name": name})  # ToolCatalog quarantines it (missing_params_schema)
            continue
        args = spec.get("args") if isinstance(spec.get("args"), dict) else {}
        parameters: dict = {
            "type": "object",
            "properties": {n: _arg_schema(a) for n, a in args.items() if isinstance(a, dict)},
        }
        required = [n for n, a in args.items() if isinstance(a, dict) and a.get("required") is True]
        if required:
            parameters["required"] = required
        tool = {"name": name, "description": str(spec.get("description", "")), "parameters": parameters}
        mutability = _KIND_TO_MUTABILITY.get(spec.get("kind"))
        if mutability is not None:
            tool["mutability"] = mutability
        out.append(tool)
    return out


class KitCodec:
    def timestamp_us(self, raw: dict) -> int | None:
        if not isinstance(raw, dict) or "timestamp_ms" not in raw:
            return None
        try:
            return int(round(float(raw["timestamp_ms"]) * 1000))
        except (TypeError, ValueError):
            return None

    def decode(self, raw: dict, *, seq: int) -> list[Envelope]:
        if not isinstance(raw, dict):
            return []
        ts_us = self.timestamp_us(raw)
        payload = raw.get("payload")
        if ts_us is None or not isinstance(payload, dict):
            return []
        event_type = raw.get("event_type")
        base_id = f"e-{seq:04d}"

        def env(payload_type: str, body, suffix: str = "") -> Envelope:
            return Envelope(
                ts_us=ts_us,
                event_class=EVENT_CLASS_BY_PAYLOAD_TYPE[payload_type],
                seq=seq,
                event_id=base_id + suffix,
                payload_type=payload_type,
                payload=body,
            )

        def text_and_eot(text, eot: bool) -> list[Envelope]:
            out: list[Envelope] = []
            if isinstance(text, str) and text.strip():
                out.append(env("text_chunk", TextChunkPayload(text=text), "-text"))
            if eot:
                out.append(env("end_of_turn", EndOfTurnPayload(), "-eot"))
            return out

        if event_type == "tool_manifest":
            tools = payload.get("tools")
            if not isinstance(tools, dict):
                return []
            return [env("manifest", ManifestPayload(tools=manifest_tools(tools)))]

        if event_type == "user_speech_chunk":
            text = payload.get("text")
            if not isinstance(text, str):
                return []
            return text_and_eot(text, payload.get("end_of_turn") is True)

        if event_type == "interruption":
            return [env("interruption", InterruptionPayload())] + text_and_eot(payload.get("text"), True)

        if event_type == "user_audio_chunk":
            ref = payload.get("audio_ref")
            if not isinstance(ref, str) or not ref:
                return []
            out = [env("audio_clip", AudioClipPayload(clip_id=ref))]
            if payload.get("end_of_turn") is True:
                out.append(env("end_of_turn", EndOfTurnPayload(), "-eot"))
            return out

        if event_type == "video_frame":
            frame_id = payload.get("frame_id") or payload.get("image_ref")
            if not isinstance(frame_id, str) or not frame_id:
                return []
            return [env("video_frame", VideoFramePayload(frame_id=frame_id))]

        if event_type == "tool_result":
            call_id = payload.get("call_id")
            status = payload.get("status")
            result = payload.get("result")
            if not isinstance(call_id, str) or status not in ("success", "error"):
                return []
            if status == "success":
                return [env("tool_result", ToolResultPayload(call_id=call_id, status="ok", result=result))]
            detail = result if isinstance(result, dict) else {}
            error = {"code": detail.get("error"), "message": detail.get("detail")}
            return [env("tool_result", ToolResultPayload(call_id=call_id, status="error", result=result, error=error))]

        return []  # scenario_end, and anything unrecognised: dropped, never raised (P-02)

    def encode(self, action: Action) -> dict:
        name = _ACTION_NAMES.get(action.action_type)
        if name is None:
            raise CodecError(f"unknown action type: {action.action_type!r}")
        body = action.body
        if isinstance(body, ToolCallBody):
            payload = {"call_id": body.call_id, "api_name": body.tool_name, "args": dict(body.arguments)}
        elif isinstance(body, CancelBody):
            payload = {"call_id": body.target_call_id}
        elif isinstance(body, (SpeakBody, FinalBody)):
            payload = {"text": body.text}
        else:
            raise CodecError(f"unknown action body type: {type(body)!r}")
        out: dict = {"action": name, "payload": payload}
        if action.snapshot is not None:
            out["state_snapshot"] = {"intent": action.snapshot.intent, "slots": dict(action.snapshot.slots)}
        return out
