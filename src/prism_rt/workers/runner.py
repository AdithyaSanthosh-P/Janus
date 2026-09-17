"""WorkerRunner: dispatches jobs outside the kernel step (K1) and surfaces
their results as WORKER_RESULT payloads — ordinary events the kernel
ingests through the normal mailbox, never awaited on directly.

`ScriptedRunner` is what every test and `SimHarness` use: deterministic,
synchronous computation, but results are withheld until a configured
latency has passed, mirroring `sim/harness.py`'s `MockToolRegistry` so a
job's result can never land in the same step it was dispatched.
`AsyncWorkerRunner` is a real asyncio implementation for optional live use
— see `workers/gateway.py`'s module docstring for why it's unverified here.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from prism_rt.model.events import WorkerResultPayload
from prism_rt.model.types import JobKind
from prism_rt.workers import composer, interpreter, planner, vision
from prism_rt.workers.gateway import ModelGateway


def _run_job(gateway: ModelGateway, kind: JobKind, view: dict) -> dict:
    if kind == JobKind.INTERPRET:
        return interpreter.run_interpret(gateway, view)
    if kind == JobKind.PLAN:
        return planner.run_plan(gateway, view)
    if kind == JobKind.COMPOSE:
        return composer.run_compose(gateway, view)
    if kind == JobKind.VISION:
        return vision.run_vision(gateway, view)
    raise ValueError(f"unknown job kind: {kind}")


class WorkerRunner(Protocol):
    def submit(self, job_id: str, kind: JobKind, view: dict, *, dispatched_us: int) -> None: ...
    def abort(self, job_id: str) -> None: ...
    def poll_results(self, now_us: int) -> list[WorkerResultPayload]: ...


class ScriptedRunner:
    def __init__(self, gateway: ModelGateway, *, latency_us_by_kind: dict[JobKind, int] | None = None) -> None:
        self._gateway = gateway
        self._latency = latency_us_by_kind or {}
        self._pending: dict[str, tuple[int, JobKind, dict]] = {}
        self._aborted: set[str] = set()

    def submit(self, job_id: str, kind: JobKind, view: dict, *, dispatched_us: int) -> None:
        due_us = dispatched_us + self._latency.get(kind, 0)
        self._pending[job_id] = (due_us, kind, view)

    def abort(self, job_id: str) -> None:
        self._aborted.add(job_id)
        self._pending.pop(job_id, None)

    def poll_results(self, now_us: int) -> list[WorkerResultPayload]:
        due_ids = sorted(jid for jid, (due_us, _kind, _view) in self._pending.items() if due_us <= now_us)
        results: list[WorkerResultPayload] = []
        for job_id in due_ids:
            due_us, kind, view = self._pending.pop(job_id)
            if job_id in self._aborted:
                continue
            try:
                proposal = _run_job(self._gateway, kind, view)
                results.append(WorkerResultPayload(job_id=job_id, kind=kind.value, status="ok", proposal=proposal))
            except Exception as exc:  # a worker failure is an expected outcome, never a crash (C3)
                results.append(
                    WorkerResultPayload(job_id=job_id, kind=kind.value, status="error", proposal={"error": str(exc)})
                )
        return results


class AsyncWorkerRunner:
    """Real async dispatch for optional live use. Not exercised by any test
    in this repository."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway
        self._queue: "asyncio.Queue[WorkerResultPayload]" = asyncio.Queue()
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, job_id: str, kind: JobKind, view: dict, *, dispatched_us: int) -> None:
        self._tasks[job_id] = asyncio.ensure_future(self._run(job_id, kind, view))

    def abort(self, job_id: str) -> None:
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _run(self, job_id: str, kind: JobKind, view: dict) -> None:
        try:
            proposal = await asyncio.to_thread(_run_job, self._gateway, kind, view)
            await self._queue.put(WorkerResultPayload(job_id=job_id, kind=kind.value, status="ok", proposal=proposal))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._queue.put(
                WorkerResultPayload(job_id=job_id, kind=kind.value, status="error", proposal={"error": str(exc)})
            )
        finally:
            self._tasks.pop(job_id, None)

    def poll_results(self, now_us: int) -> list[WorkerResultPayload]:
        results = []
        while not self._queue.empty():
            results.append(self._queue.get_nowait())
        return results
