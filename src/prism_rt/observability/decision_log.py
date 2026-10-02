"""DecisionLogger: one structured record per kernel step, written as JSONL.

The harness trace shows what happened; this shows why — every emitted or
rejected action links back to the rule that produced it
(`docs/prompt 2.txt` §16.2, K6). Appending here never affects a decision
(`observability` may only import `model`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _value(x):
    return getattr(x, "value", x)


def _batch_entry(env) -> dict[str, Any]:
    entry = {"seq": env.seq, "type": env.payload_type, "event_id": env.event_id, "ts_us": env.ts_us}
    payload = env.payload
    if env.payload_type == "worker_result":
        # What the model proposed, before the kernel accepted or dropped it.
        entry.update(job_id=payload.job_id, kind=payload.kind, status=payload.status, proposal=payload.proposal)
    elif env.payload_type == "tool_result":
        entry.update(call_id=payload.call_id, status=payload.status)
    elif env.payload_type == "text_chunk":
        entry["text"] = payload.text
    elif env.payload_type == "user_speech":
        entry["active"] = payload.active
    return entry


class DecisionLogger:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._file = self._path.open("a", encoding="utf-8") if self._path else None
        self._current: dict[str, Any] | None = None
        self.records: list[dict[str, Any]] = []

    def begin(self, *, step_no: int, now_us: int, batch) -> None:
        self._current = {
            "step_no": step_no,
            "now_us": now_us,
            "batch_size": len(batch),
            "batch": [_batch_entry(env) for env in batch],
            "notes": [],
        }

    def note(self, message: str) -> None:
        if self._current is not None:
            self._current["notes"].append(message)

    def end(
        self,
        *,
        step_no: int,
        now_us: int,
        change_set,
        invalidation,
        emit_report,
        gate_rejections=(),
        dispatched=(),
    ) -> dict[str, Any]:
        record = self._current if self._current is not None else {
            "step_no": step_no,
            "now_us": now_us,
            "batch_size": 0,
            "batch": [],
            "notes": [],
        }

        record["fact_changes"] = [
            {
                "key": change[0],
                "old_digest": change[1],
                "new_digest": change[2],
                "old_status": change[3].value if change[3] is not None else None,
                "new_status": change[4].value if hasattr(change[4], "value") else change[4],
                "ver": change[5],
                "rule": change[6],
            }
            for change in change_set.changes
        ]
        record["invalidations"] = {
            "changed_keys": sorted(invalidation.changed_keys),
            "invalidated_call_ids": list(invalidation.invalidated_call_ids),
            "discarded_call_ids": list(invalidation.discarded_call_ids),
        }
        record["emitted"] = [
            {
                "action_id": er.action.action_id,
                "action_type": er.action.action_type.value,
                "rule_id": er.action.rule_id,
            }
            for er in emit_report.emitted
        ]
        record["rejected"] = [
            {"action_type": rr.intended.action_type.value, "reason": rr.reason_code}
            for rr in emit_report.rejected
        ]
        # Why a proposed call is still waiting (CommitGate), and which model
        # jobs this step started -- found needed by the 1 Oct audit, which
        # could not tell a model error from a kernel hold in a live log.
        record["gate_rejections"] = [
            {"call_id": gr.call_id, "tool": gr.tool, "rule": gr.rule_id, "reason": gr.blocked_reason} for gr in gate_rejections
        ]
        record["dispatched"] = [{"job_id": req.job_id, "kind": _value(req.kind)} for req in dispatched]
        self._write(record)
        self._current = None
        return record

    def error(self, *, step_no: int | None, now_us: int | None, message: str) -> dict[str, Any]:
        """A step that raised: the batch it was handling (if `begin` ran)
        and the error, instead of a silent gap in the log."""
        record = self._current if self._current is not None else {
            "step_no": step_no,
            "now_us": now_us,
            "batch_size": 0,
            "batch": [],
            "notes": [],
        }
        record["error"] = message
        self._write(record)
        self._current = None
        return record

    def _write(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self._file is not None:
            self._file.write(json.dumps(record, default=str) + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
