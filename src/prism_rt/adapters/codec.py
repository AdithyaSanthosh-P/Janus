"""HarnessCodec: wire JSON <-> internal Envelope/Action.

The only place wire field names appear (`docs/prompt 2.txt` §2.1,
ProtocolCodec row: "the only place wire field names appear" / "Must never:
Hold state"). The real evaluation kit's wire format is unreleased
(`currentStatus.md` known risks), so this codec targets a provisional
schema shaped by the scenario YAML in
`docs/sonnet_implementation_plan.md` §6.2 — swap the body of `decode`/
`encode` when the kit's real schema lands; callers outside this module
never need to change.

`decode` is deliberately tolerant, not strict (P-02, `docs/post_v4_
implementation_plan.md` finding F2): our guessed schema is exactly that —
a guess — so an event this codec can't make sense of is dropped (returns
`[]`), never raised. Unknown payload fields are ignored rather than
rejected, a small set of plausible field-name variants are accepted
alongside the canonical names, and both the nested `{"payload": {...}}`
shape and a flat shape (fields alongside `type`/`ts_us`) are accepted.
`encode` stays strict — it's our own output, fully under our control, so
there's no excuse for it being malformed.

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
    "video_frame": ("frame_id",),
    "audio_clip": ("clip_id",),
    "timer_fired": ("timer_id", "timer_kind"),
    "watchdog": ("wall_elapsed_s",),
}

# Plausible dialect variants for the event's own `type` field.
_TYPE_ALIASES: dict[str, str] = {
    "chunk": "text_chunk",
    "eot": "end_of_turn",
    "end_turn": "end_of_turn",
    "interrupt": "interruption",
    "frame": "video_frame",
    "audio": "audio_clip",
}

# Plausible dialect variants for payload field names, by canonical event type.
_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "manifest": {"manifest": "tools"},
    "text_chunk": {"content": "text", "message": "text"},
    "tool_result": {"id": "call_id"},
    "worker_result": {"id": "job_id"},
    "video_frame": {"id": "frame_id"},
    "audio_clip": {"id": "clip_id"},
}

# Keys that belong to the envelope, not the payload — excluded when falling
# back to a flat (non-nested-payload) event shape.
_ENVELOPE_LEVEL_KEYS = frozenset({"type", "ts_us", "ts_ms", "timestamp", "ts", "event_id"})


class HarnessCodec:
    def decode(self, raw: dict, *, seq: int) -> list[Envelope]:
        if not isinstance(raw, dict):
            return []

        payload_type = raw.get("type")
        payload_type = _TYPE_ALIASES.get(payload_type, payload_type)
        if payload_type not in PAYLOAD_TYPES:
            return []

        ts_us = self._extract_ts(raw)
        if ts_us is None:
            return []

        if "payload" in raw:
            payload_dict = raw.get("payload") or {}
        else:
            payload_dict = {k: v for k, v in raw.items() if k not in _ENVELOPE_LEVEL_KEYS}
        if not isinstance(payload_dict, dict):
            return []

        payload_dict = self._apply_field_aliases(payload_type, payload_dict)

        missing = [f for f in _REQUIRED_FIELDS[payload_type] if f not in payload_dict]
        if missing:
            return []

        payload_cls = PAYLOAD_TYPES[payload_type]
        fields = dataclasses.fields(payload_cls)
        field_names = {f.name for f in fields}
        payload_dict = {k: v for k, v in payload_dict.items() if k in field_names}

        # Dataclasses don't validate types at construction — a required
        # `str` field handed e.g. an int would otherwise construct fine
        # and crash much later, deep in a reducer (found by testing this
        # exact case: TextChunkPayload(text=12345) succeeds, then
        # `" ".join(...)` over chunk text blows up in TurnManager). Only
        # the plain, unambiguous `str` annotation is checked — unions and
        # non-string types are left alone rather than guessing a coercion.
        for f in fields:
            if f.type == "str" and f.name in payload_dict and not isinstance(payload_dict[f.name], str):
                return []

        try:
            payload = payload_cls(**payload_dict)
        except (TypeError, ValueError):
            return []

        event_id = raw.get("event_id") or raw.get("id") or f"e-{seq:04d}"
        event_class = EVENT_CLASS_BY_PAYLOAD_TYPE[payload_type]
        envelope = Envelope(
            ts_us=ts_us,
            event_class=event_class,
            seq=seq,
            event_id=str(event_id),
            payload_type=payload_type,
            payload=payload,
        )
        return [envelope]

    def timestamp_us(self, raw: dict) -> int | None:
        """The event's timestamp in microseconds, or None -- what
        `entry.py` advances the clock to before stepping."""
        return self._extract_ts(raw)

    def _extract_ts(self, raw: dict) -> int | None:
        if "ts_us" in raw:
            try:
                return int(raw["ts_us"])
            except (TypeError, ValueError):
                return None
        # ts_ms/timestamp/ts are all treated as milliseconds — the only unit
        # convention this codec has ever had beyond native microseconds.
        for key in ("ts_ms", "timestamp", "ts"):
            if key in raw:
                try:
                    return int(raw[key]) * 1000
                except (TypeError, ValueError):
                    return None
        return None

    def _apply_field_aliases(self, payload_type: str, payload_dict: dict) -> dict:
        aliases = _FIELD_ALIASES.get(payload_type)
        if not aliases:
            return payload_dict
        out = dict(payload_dict)
        for alias, canonical in aliases.items():
            if canonical not in out and alias in out:
                out[canonical] = out.pop(alias)
        return out

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
