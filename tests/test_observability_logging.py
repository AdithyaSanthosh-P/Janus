"""1 Oct audit: two gaps that made live failures unattributable.

1. `entry.py`'s driver swallowed any exception from decoding or a kernel
   step without a trace -- in a live run that looked exactly like the agent
   deciding to stay silent.
2. The decision log recorded neither what a model proposed nor why the
   CommitGate held a call, so a dropped value could not be pinned on the
   model or the kernel."""

from __future__ import annotations

import asyncio
import json
import logging

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.entry import setup
from prism_rt.model.types import JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

TRACK = {"name": "track_order", "parameters": {"type": "object", "properties": {
    "order_id": {"type": "string"}}, "required": ["order_id"]}}
INTERP = {"act": "new_goal", "intent": "track_order", "slot_deltas": [],
          "actions": [{"tool": "track_order", "args": {"order_id": "BOB12"}}]}


def test_a_failing_kernel_step_is_logged_not_swallowed(tmp_path, caplog, monkeypatch):
    import prism_rt.kernel.step as step_module

    real_apply = step_module.apply_reducer

    def apply_or_fail(env, txn, now_us, step_no):
        if env.payload_type == "text_chunk":
            raise RuntimeError("reducer exploded")
        return real_apply(env, txn, now_us, step_no)

    monkeypatch.setattr(step_module, "apply_reducer", apply_or_fail)
    log_path = tmp_path / "decisions.jsonl"

    async def go():
        runtime = setup(config=fdb_v3_config(watchdog_timeout_ms=0), provider=ScriptedProvider())
        events, actions = asyncio.Queue(), asyncio.Queue()
        task = asyncio.create_task(runtime.run_scenario(events, actions, meta={"log_path": str(log_path)}))
        await events.put({"type": "manifest", "ts_us": 0, "payload": {"tools": [TRACK]}})
        await events.put({"type": "text_chunk", "ts_us": 1000, "payload": {"text": "track order BOB12"}})
        await asyncio.sleep(0.1)
        await events.put(None)
        await asyncio.wait_for(task, 5)

    with caplog.at_level(logging.ERROR, logger="janus.runtime"):
        asyncio.run(go())

    assert any("kernel step failed" in r.getMessage() and r.exc_info for r in caplog.records)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    errors = [r for r in records if "error" in r]
    assert len(errors) == 1 and "reducer exploded" in errors[0]["error"]
    assert errors[0]["batch"][0]["type"] == "text_chunk"  # the batch it was handling


def test_the_decision_log_records_proposals_dispatches_and_gate_holds():
    provider = ScriptedProvider()
    provider.register("interpret", "BOB12", INTERP)
    provider.register("compose", "", {"text": "It is on its way.", "claims": []})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"track_order": {"latency_ms": 100, "response": {"status": "shipped"}}},
                   worker_latency_us={k: 10_000 for k in JobKind})
    h.send(0, [manifest_event([TRACK])])
    h.send(100_000, [chunk_event("track order BOB12")])
    h.send(150_000, [eot_event()])
    drain(h, 3_000_000)
    records = h.log.records

    dispatched = [d for r in records for d in r["dispatched"]]
    assert dispatched and dispatched[0]["kind"] == "interpret" and dispatched[0]["job_id"]

    proposals = [b for r in records for b in r["batch"] if b["type"] == "worker_result" and b["kind"] == "interpret"]
    assert proposals and proposals[0]["proposal"]["actions"] == INTERP["actions"]

    holds = [g for r in records for g in r["gate_rejections"]]
    assert holds and holds[0]["tool"] == "track_order" and holds[0]["rule"] == "G10"  # the settle window

    chunks = [b for r in records for b in r["batch"] if b["type"] == "text_chunk"]
    assert chunks[0]["text"] == "track order BOB12"
