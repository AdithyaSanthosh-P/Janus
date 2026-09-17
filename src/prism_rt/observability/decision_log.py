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
            "batch": [
                {"seq": env.seq, "type": env.payload_type, "event_id": env.event_id, "ts_us": env.ts_us}
                for env in batch
            ],
            "notes": [],
        }

    def note(self, message: str) -> None:
        if self._current is not None:
            self._current["notes"].append(message)

    def end(self, *, step_no: int, now_us: int, change_set, invalidation, emit_report) -> dict[str, Any]:
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

        self.records.append(record)
        if self._file is not None:
            self._file.write(json.dumps(record, default=str) + "\n")
            self._file.flush()
        self._current = None
        return record

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
