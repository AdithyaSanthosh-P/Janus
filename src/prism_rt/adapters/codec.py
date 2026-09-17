"""HarnessCodec: wire JSON <-> internal Envelope/Action.

The only place wire field names appear (`docs/prompt 2.txt` §2.1,
ProtocolCodec row: "the only place wire field names appear" / "Must never:
Hold state"). The real evaluation kit's wire format is unreleased
(`currentStatus.md` known risks), so this codec targets a provisional
schema shaped by the scenario YAML in
`docs/sonnet_implementation_plan.md` §6.2 — swap the body of `decode`/
`encode` when the kit's real schema lands; callers outside this module
never need to change.

Deviation from the plan's one-argument `decode(raw) -> list[Envelope]`
signature: ProtocolCodec must hold no state (per the architecture doc), so
`seq` (arrival order) has to come from the caller — Ingress/SimHarness is
the only component that knows it. `decode` therefore takes `seq` as a
required keyword argument.
"""

from __future__ import annotations

import dataclasses

from prism_rt.errors import CodecError
from prism_rt.model.actions import Action, CancelBody, FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.events import EVENT_CLASS_BY_PAYLOAD_TYPE, PAYLOAD_TYPES, Envelope

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "manifest": ("tools",),
    "text_chunk": ("text",),
    "end_of_turn": (),
    "interruption": (),
    "tool_result": ("call_id", "status"),
    "worker_result": ("job_id", "kind", "status"),
    "timer_fired": ("timer_id", "timer_kind"),
    "watchdog": ("wall_elapsed_s",),
}


class HarnessCodec:
    def decode(self, raw: dict, *, seq: int) -> list[Envelope]:
        if not isinstance(raw, dict):
            raise CodecError(f"event is not an object: {raw!r}")

        payload_type = raw.get("type")
        if payload_type not in PAYLOAD_TYPES:
            raise CodecError(f"unknown event type: {payload_type!r}")

        if "ts_us" in raw:
            ts_us = int(raw["ts_us"])
        elif "ts_ms" in raw:
            ts_us = int(raw["ts_ms"]) * 1000
        else:
            raise CodecError("event missing ts_us/ts_ms")

        payload_dict = raw.get("payload") or {}
        if not isinstance(payload_dict, dict):
            raise CodecError(f"{payload_type} payload is not an object")

        missing = [f for f in _REQUIRED_FIELDS[payload_type] if f not in payload_dict]
        if missing:
            raise CodecError(f"{payload_type} missing required fields: {missing}")

        payload_cls = PAYLOAD_TYPES[payload_type]
        field_names = {f.name for f in dataclasses.fields(payload_cls)}
        unknown = [k for k in payload_dict if k not in field_names]
        if unknown:
            raise CodecError(f"{payload_type} has unknown fields: {unknown}")

        try:
            payload = payload_cls(**payload_dict)
        except TypeError as exc:
            raise CodecError(f"bad payload for {payload_type}: {exc}") from exc

        event_id = raw.get("event_id") or f"e-{seq:04d}"
        event_class = EVENT_CLASS_BY_PAYLOAD_TYPE[payload_type]
        envelope = Envelope(
            ts_us=ts_us,
            event_class=event_class,
            seq=seq,
            event_id=event_id,
            payload_type=payload_type,
            payload=payload,
        )
        return [envelope]

    def encode(self, action: Action) -> dict:
        body = action.body
        out: dict = {
            "action_id": action.action_id,
            "action_type": action.action_type.value,
            "ts_us": action.ts_us,
            "trigger_event_id": action.trigger_event_id,
            "rule_id": action.rule_id,
        }

        if isinstance(body, SpeakBody):
            out["body"] = {
                "text": body.text,
                "kind": body.kind,
                "claim_grade": body.claim_grade.value if body.claim_grade else None,
            }
        elif isinstance(body, ToolCallBody):
            out["body"] = {
                "call_id": body.call_id,
                "tool_name": body.tool_name,
                "arguments": body.arguments,
                "mutability": body.mutability.value,
            }
        elif isinstance(body, CancelBody):
            out["body"] = {"target_call_id": body.target_call_id, "reason": body.reason}
        elif isinstance(body, FinalBody):
            out["body"] = {"text": body.text, "task_completed": body.task_completed}
        else:
            raise CodecError(f"unknown action body type: {type(body)!r}")

        if action.snapshot is not None:
            out["snapshot"] = {
                "intent": action.snapshot.intent,
                "slots": action.snapshot.slots,
                "revision": action.snapshot.revision,
                "digest": action.snapshot.digest,
            }
        return out
