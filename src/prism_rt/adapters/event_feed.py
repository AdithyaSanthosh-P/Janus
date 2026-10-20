"""EventFeed: the companion-app event stream (app/INTEGRATION.md, v1).

Pure translation, like `VoiceBridge`: it reads what the kernel already did --
each step's batch, gate rejections and emitted actions, plus the call ledger --
and publishes it as the contract's JSON events through an `emit(dict)`
callback. It decides nothing and never touches the store. The voice worker
(`voice/agent.py`) supplies `emit` (a LiveKit data packet on topic
`janus.events`); tests supply a list. A failing `emit` is logged, never raised,
so the app can never break the conversation.

Host-side inputs the kernel cannot know: the filler phrase (`agent_said`
with kind "filler") and device reads (`device_state`).

Imports no livekit.
"""

from __future__ import annotations

import logging
from typing import Callable

from config.templates import ACK_DEFAULT
from prism_rt.model.types import ActionType, CallStatus, StepKind

log = logging.getLogger("prism_rt.event_feed")

SCHEMA_VERSION = 1

# Required fields per event type, beyond the common v / seq / t_ms / type.
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "session_started": ("packs", "backend"),
    "user_speaking": ("active",),
    "heard": ("turn_id", "text"),
    "turn_closed": ("turn_id",),
    "agent_said": ("text", "kind"),
    "agent_interrupted": (),
    "call_planned": ("call_id", "tool", "label", "kind", "args", "security"),
    "call_held": ("call_id", "reason_code", "reason"),
    "confirmation_asked": ("call_id", "question"),
    "call_cancelled": ("call_id", "reason"),
    "call_sent": ("call_id", "args"),
    "call_result": ("call_id", "outcome", "summary"),
    "device_state": ("device", "label", "capability", "attribute", "value", "backend"),
    "goal_finished": ("goal_id", "completed", "summary"),
    "ledger_summary": ("committed", "withdrawn_before_sending", "unconfirmed", "failed", "duplicates_blocked"),
}

HELD_REASONS = {
    "user_speaking": "Waiting for you to finish speaking",
    "settling": "Waiting a moment in case you change your mind",
    "needs_go_ahead": "Waiting for your go-ahead",
    "needs_confirmation": "Security action: waiting for your yes",
    "duplicate": "Already done in this conversation; not repeating it",
    "outcome_unknown": "An earlier attempt was not confirmed; not resending blindly",
    "other": "Waiting",
}

_REASON_CODE = {
    "user_speaking": "user_speaking", "floor_open": "user_speaking", "interpretation_pending": "user_speaking",
    "settle_not_elapsed": "settling", "new_turn_open": "settling",
    "commit_intent_false": "needs_go_ahead",
    "confirmation_required": "needs_confirmation",
    "duplicate_fingerprint": "duplicate", "duplicate_lineage": "duplicate",
    "unknown_effect_outcome": "outcome_unknown",
}

_GONE = (CallStatus.INVALIDATED, CallStatus.DISCARDED, CallStatus.CANCEL_REQUESTED, CallStatus.CANCELLED)
_FAILURE_FLAGS = ("done", "booked", "opened")  # a result that says False for one of these was refused


class _Safe(dict):
    def __missing__(self, key):
        return "?"


def _words(tool: str) -> str:
    return tool.removeprefix("st_").replace("_", " ")


class EventFeed:
    def __init__(
        self,
        emit: Callable[[dict], None],
        *,
        backend: str,
        packs: list[str],
        manifest: list[dict],
        clock_ms: Callable[[], int],
    ) -> None:
        self._emit = emit
        self._backend = backend
        self._packs = packs
        self._manifest = {t["name"]: t for t in manifest if isinstance(t, dict) and "name" in t}
        self._clock_ms = clock_ms
        self._seq = 0
        self._planned: set[str] = set()
        self._held: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._asked: set[str] = set()
        self._sent: set[str] = set()
        self._dup_blocked: set[str] = set()
        self._filler_turn = False
        self._turn_ids: dict[str, bool] = {}

    # ---- publishing -----------------------------------------------------
    def _send(self, type_: str, **fields) -> None:
        missing = set(EVENT_FIELDS[type_]) - set(fields)  # KeyError for an unknown type, on purpose
        if missing:
            raise ValueError(f"{type_} is missing {sorted(missing)}")
        self._seq += 1
        event = {"v": SCHEMA_VERSION, "seq": self._seq, "t_ms": int(self._clock_ms()), "type": type_, **fields}
        try:
            self._emit(event)
        except Exception:  # noqa: BLE001 - the app must never break the conversation
            log.warning("event feed: could not publish %s", type_, exc_info=True)

    # ---- host-side inputs -------------------------------------------------
    def session_started(self) -> None:
        self._send("session_started", packs=list(self._packs), backend=self._backend)

    def agent_said(self, text: str, kind: str) -> None:
        if kind == "filler":
            self._filler_turn = True
        self._send("agent_said", text=text, kind=kind)

    def device_state(self, device: str, label: str, capability: str, attribute: str, value,
                     backend: str | None = None) -> None:
        self._send("device_state", device=device, label=label, capability=capability, attribute=attribute,
                   value=value, backend=backend or self._backend)

    # ---- kernel steps -------------------------------------------------------
    def on_step(self, report, store) -> None:
        try:
            self._on_step(report, store)
        except Exception:  # noqa: BLE001 - never let the feed break a step
            log.exception("event feed: step %s could not be translated", getattr(report, "step_no", "?"))

    def _on_step(self, report, store) -> None:
        results = []
        for env in report.batch:
            kind, payload = env.payload_type, env.payload
            if kind == "text_chunk":
                text = (getattr(payload, "text", "") or "").strip()
                if text:
                    self._filler_turn = False  # a new turn's words: its ACK is its own
                    self._send("heard", turn_id=self._turn_id(store), text=text)
            elif kind == "end_of_turn":
                self._send("turn_closed", turn_id=self._turn_id(store))
            elif kind == "user_speech":
                self._send("user_speaking", active=bool(getattr(payload, "active", False)))
            elif kind == "interruption":
                self._send("agent_interrupted")
            elif kind == "tool_result":
                results.append(payload)

        calls = store.call_ledger.all()
        self._planned_events(calls, store)
        for rejection in getattr(report, "gate_rejections", ()):
            self._held_event(rejection)
        self._cancelled_events(calls)

        for emitted in report.emit_report.emitted:
            self._action_events(emitted.action, store, calls)

        for payload in results:
            self._result_event(payload, store)
        if any(e.action.action_type == ActionType.FINAL for e in report.emit_report.emitted):
            self._ledger_summary(store)

    def _turn_id(self, store) -> str:
        turns = store.turn_log.all()
        return turns[-1].turn_id if turns else "t-0000"

    def _label(self, tool: str, args: dict) -> str:
        template = (self._manifest.get(tool) or {}).get("label")
        if template:
            return template.format_map(_Safe(args))
        values = ", ".join(str(v) for v in args.values())
        return f"{_words(tool).capitalize()}" + (f" ({values})" if values else "")

    def _planned_events(self, calls, store) -> None:
        for call in calls:
            if call.call_id in self._planned:
                continue
            self._planned.add(call.call_id)
            spec = store.catalog.get(call.tool)
            self._send(
                "call_planned", call_id=call.call_id, tool=call.tool, label=self._label(call.tool, call.args),
                kind="write" if call.kind == StepKind.WRITE else "read", args=dict(call.args),
                security=bool(spec is not None and spec.requires_confirmation),
            )

    def _held_event(self, rejection) -> None:
        code = _REASON_CODE.get(rejection.blocked_reason or "", "other")
        if code == "duplicate":
            self._dup_blocked.add(rejection.call_id)
        if self._held.get(rejection.call_id) == code:
            return
        self._held[rejection.call_id] = code
        self._send("call_held", call_id=rejection.call_id, reason_code=code, reason=HELD_REASONS[code])

    def _cancelled_events(self, calls) -> None:
        for call in calls:
            if call.status in _GONE and call.call_id in self._planned and call.call_id not in self._cancelled:
                self._cancelled.add(call.call_id)
                self._send("call_cancelled", call_id=call.call_id, reason=self._cancel_reason(call, calls))

    @staticmethod
    def _cancel_reason(call, calls) -> str:
        """"You changed it to 26" when a later call of the same tool replaced it."""
        for later in calls:
            if later.call_id > call.call_id and later.tool == call.tool and later.goal_id == call.goal_id and later.args != call.args:
                changed = [str(v) for k, v in later.args.items() if call.args.get(k) != v]
                if changed:
                    return "You changed it to " + ", ".join(changed)
        return "Dropped because your request changed"

    def _action_events(self, action, store, calls) -> None:
        kind = action.action_type
        if kind in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
            body = action.body
            text = getattr(body, "text", "") or ""
            if not text:
                return
            said_kind = "final" if kind == ActionType.FINAL else "clarify" if kind == ActionType.CLARIFY else (
                "ack" if getattr(body, "kind", "") == "ack" else "inform")
            if said_kind == "ack" and self._filler_turn and text == ACK_DEFAULT:
                self._filler_turn = False  # the host drops this ACK after a filler: nothing was said
                return
            if said_kind != "final":
                self._filler_turn = False
            self._send("agent_said", text=text, kind=said_kind)
            if action.rule_id == "responder.confirm_write":
                for call in calls:
                    if call.status == CallStatus.PROPOSED and call.call_id not in self._asked:
                        asked = store.facts.get(f"confirm.{call.call_id}.asked")
                        if asked is not None and asked.value is True:
                            self._asked.add(call.call_id)
                            self._send("confirmation_asked", call_id=call.call_id, question=text)
                            break
            if kind == ActionType.FINAL:
                self._send("goal_finished", goal_id=self._finished_goal(store), completed=bool(getattr(body, "task_completed", True)),
                           summary=text)
        elif kind == ActionType.TOOL_CALL:
            body = action.body
            if body.call_id not in self._sent:
                self._sent.add(body.call_id)
                self._send("call_sent", call_id=body.call_id, args=dict(body.arguments))

    @staticmethod
    def _finished_goal(store) -> str:
        fact = store.facts.get("session.last_finished_goal")
        if fact is not None and fact.value:
            return str(fact.value)
        goals = store.goals.all()
        return goals[-1].goal_id if goals else ""

    def _result_event(self, payload, store) -> None:
        call = store.call_ledger.get(payload.call_id)
        if call is None or call.call_id not in self._planned:
            return
        status, result = payload.status, payload.result
        if status == "unknown":
            outcome, summary = "unconfirmed", "Sent, but SmartThings did not confirm the change"
        elif status == "error":
            err = payload.error or (result if isinstance(result, dict) else {})
            outcome, summary = "failed", str((err or {}).get("error") or (err or {}).get("message") or "It failed")
        elif isinstance(result, dict) and any(result.get(k) is False for k in _FAILURE_FLAGS):
            outcome, summary = "refused", str(result.get("message") or "The device refused it")
        elif isinstance(result, dict) and result.get("already"):
            outcome, summary = "already", str(result.get("note") or "Already in that state; nothing was sent")
        else:
            outcome = "confirmed"
            summary = " ".join(str(result[k]) for k in ("device", "state") if isinstance(result, dict) and result.get(k) is not None) or "Done"
        self._send("call_result", call_id=call.call_id, outcome=outcome, summary=summary)

    def _ledger_summary(self, store) -> None:
        from prism_rt.model.types import EffectStatus
        from prism_rt.observability.effects import effect_summary

        base = effect_summary(store)
        self._send(
            "ledger_summary",
            committed=base["committed"],
            withdrawn_before_sending=base["withdrawn_before_sending"],
            unconfirmed=sum(e.status == EffectStatus.UNKNOWN for e in store.effect_ledger.all()),
            failed=base["refused_or_failed"],
            duplicates_blocked=len(self._dup_blocked),
        )
