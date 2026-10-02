"""What a session actually did to the world: the effect-ledger summary.

`effect_summary(store)` counts, from the kernel's own ledgers, the writes that
went through, the ones withdrawn before they were sent (a correction or a
change of mind invalidated them), the ones the service refused or that failed,
and any write sent twice with the same tool and arguments -- which the
CommitGate exists to make impossible, so a non-zero count means a bug.

A refusal is a result whose own success flag is false (`{"done": False}`,
`{"booked": False}`): the call worked, the device or service said no. Printed at the end of the
device-care demo and logged per session by the voice worker.
"""

from __future__ import annotations

from prism_rt.model.types import CallStatus, EffectStatus, StepKind

_SUCCESS_FLAGS = ("done", "booked", "opened")
_WITHDRAWN = {CallStatus.CANCELLED, CallStatus.INVALIDATED, CallStatus.STALE, CallStatus.DISCARDED}


def effect_summary(store) -> dict:
    writes = [c for c in store.call_ledger.all() if c.kind == StepKind.WRITE]
    sent = [c for c in writes if c.emitted_ts_us is not None]
    effects = store.effect_ledger.all()
    refused = 0
    for e in effects:
        value = getattr(store.facts.get(f"result.{e.call_id}"), "value", None)
        if e.status == EffectStatus.CONFIRMED and isinstance(value, dict) and any(value.get(k) is False for k in _SUCCESS_FLAGS):
            refused += 1
    seen: set[str] = set()
    duplicates = 0
    for call in sorted(sent, key=lambda c: c.emitted_ts_us):
        duplicates += call.fingerprint in seen
        seen.add(call.fingerprint)
    return {
        "committed": sum(e.status == EffectStatus.CONFIRMED for e in effects) - refused,
        "withdrawn_before_sending": sum(c.emitted_ts_us is None and c.status in _WITHDRAWN for c in writes),
        "refused_or_failed": refused + sum(e.status == EffectStatus.FAILED for e in effects),
        "duplicates": duplicates,
    }


def format_effect_summary(summary: dict) -> str:
    return (f"Effect ledger: {summary['committed']} committed, {summary['withdrawn_before_sending']} withdrawn "
            f"before sending, {summary['refused_or_failed']} refused or failed, {summary['duplicates']} duplicates")
