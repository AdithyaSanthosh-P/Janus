"""Reducers: event -> state mutation, dispatched by `Envelope.payload_type`.

V0 handles `manifest` (-> catalog) and `tool_result` (-> ResultRouter,
which does the actual fact/ledger mutation). Every other payload type is a
V1+ concern (turn assembly, worker results, timers) and is a documented
no-op here until `kernel/turns.py`, `kernel/task.py`, etc. land.
"""

from __future__ import annotations

from prism_rt.kernel.results import ResultRouter
from prism_rt.model.events import Envelope, ManifestPayload, ToolResultPayload
from prism_rt.model.types import FactStatus, Provenance
from prism_rt.store.session import StoreTxn

_RESULT_ROUTER = ResultRouter()


def _apply_manifest(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: ManifestPayload = env.payload
    txn.catalog.parse_manifest(payload.tools)
    txn.facts.set(
        "catalog.version",
        txn.catalog.version,
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=env.event_id, step_no=step_no, ts_us=now_us),
        rule="reducers.manifest",
    )


def _apply_tool_result(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: ToolResultPayload = env.payload
    _RESULT_ROUTER.route(payload, txn.store, now_us, step_no=step_no, event_id=env.event_id)


_HANDLERS = {
    "manifest": _apply_manifest,
    "tool_result": _apply_tool_result,
}


def apply(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    handler = _HANDLERS.get(env.payload_type)
    if handler is None:
        return
    handler(env, txn, now_us, step_no)
