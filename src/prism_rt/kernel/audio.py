"""AsrScheduler: audio clip -> transcript segments -> ordinary text chunks.

`docs/prompt 2.txt` §9.3, simplified per `docs/post_v4_implementation_plan.md`
Phase 2's own scope note. The full spec's `auto` mode holds ASR output for
`asr_dedupe_ms` of harness time and only discards it if a text chunk
arrives *within that window*, using a timer. This implementation instead
checks, the moment an ASR result actually resolves, whether the open turn
already has any chunks in it — if so, a real transcript is assumed to
already be covering this utterance and the ASR output is discarded (with
a `asr.<obs_id>.disagreement` fact recorded, the signal `tests/
test_audio.py`'s M-01 case checks). This is deliberately less precise
than a real timed dedupe window (a text chunk arriving *after* the ASR
result, but still describing the same utterance, isn't caught), but it's
correct for what it does cover, adds no new timer machinery, and every
`audio_only` scenario (no text ever arrives) is unaffected either way.

Two-phase, same split as `kernel/perception.py`: `decide()` dispatches
(called from `kernel/step.py`'s DECIDE phase, store-only mutations, no
`txn` needed — mirrors `PerceptionScheduler.decide`); `on_asr_result()`
applies a resolved job's transcript (called from `kernel/reducers.py`'s
`_apply_worker_result`, APPLY phase, `txn` already in scope — mirrors
`on_perception_result`).
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.kernel.turns import TurnManager
from prism_rt.model.types import AsrSegment, FactStatus, JobKind, JobRecord, Provenance, ReadSet

_TURN_MANAGER = TurnManager()


@dataclass(frozen=True)
class AsrDispatchRequest:
    """Same shape as `kernel.task.DispatchRequest` — duck-typed by
    `Kernel.step`'s DISPATCH phase, which only reads `.job_id`/`.kind`/
    `.view`. A separate class (not imported from `kernel.task`) only to
    match `kernel/perception.py`'s existing precedent for this kind of
    request, not because of any real import-cycle risk here."""

    job_id: str
    kind: JobKind
    view: dict
    goal_id: str | None
    turn_id: str | None
    read_set: ReadSet


class AsrScheduler:
    # --- dispatch (called from kernel/step.py, DECIDE phase) ---

    def decide(self, store, now_us: int, step_no: int) -> list[AsrDispatchRequest]:
        if not store.config.asr_enabled or store.config.audio_mode == "transcript_primary":
            return []
        requests: list[AsrDispatchRequest] = []
        for obs in store.evidence.observations_by_modality("audio"):
            if obs.asr_done or obs.asr_job_id is not None:
                continue
            job_id = store.ids.next("job")
            read_set = store.facts.build_read_set([])  # not goal-scoped; a transcript stays valid regardless of later goal changes
            view = {"obs_id": obs.obs_id, "frame_id": obs.frame_id}  # harness-given clip reference — see workers/asr.py's module docstring
            store.jobs.create(JobRecord(job_id=job_id, kind=JobKind.ASR, goal_id=None, turn_id=None, read_set=read_set))
            store.evidence.update_observation(obs.obs_id, asr_job_id=job_id)
            requests.append(AsrDispatchRequest(job_id=job_id, kind=JobKind.ASR, view=view, goal_id=None, turn_id=None, read_set=read_set))
        return requests

    # --- result application (called from kernel/reducers.py, APPLY phase) ---

    def on_asr_result(
        self,
        txn,
        job,
        segments: tuple[AsrSegment, ...],
        end_of_utterance: bool,
        now_us: int,
        step_no: int,
        *,
        event_id: str,
    ) -> None:
        obs = self._obs_for_job(txn.store, job.job_id)
        if obs is None:
            return
        txn.evidence.update_observation(obs.obs_id, asr_done=True, asr_job_id=None)

        audio_mode = txn.store.config.audio_mode
        if audio_mode == "auto":
            turn_id = txn.turn_log.open_turn_id()
            turn = txn.turn_log.get(turn_id) if turn_id is not None else None
            if turn is not None and turn.chunks:
                # A real transcript already covers this utterance — discard
                # rather than double-append (M-01).
                txn.facts.set(
                    f"asr.{obs.obs_id}.disagreement",
                    True,
                    FactStatus.COMMITTED,
                    Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
                    rule="audio.asr_disagreement",
                )
                return

        gid = active_goal_id(txn.store)
        last_end_us = obs.capture_ts_us
        for seg in segments:
            seg_ts = obs.capture_ts_us + seg.offset_us
            _TURN_MANAGER.on_chunk(seg.text, event_id, seg_ts, txn, active_goal_id=gid, step_no=step_no)
            last_end_us = max(last_end_us, obs.capture_ts_us + seg.end_us)

        if audio_mode == "audio_only" and txn.store.config.asr_closes_turn and end_of_utterance:
            turn = _TURN_MANAGER.on_eot(last_end_us, txn)
            _TURN_MANAGER.request_interpretation(txn, turn, last_end_us, step_no, event_id=event_id)

    def release_dropped_job(self, txn, job, now_us: int, step_no: int, *, event_id: str) -> None:
        """An ASR job that errored never reaches `on_asr_result`, so without
        this its observation kept `asr_job_id` set forever and `decide`
        never retried it -- an audio-only utterance was silently lost after
        a single failure (e.g. a live 429 surviving the provider's own
        retries). Transcription is idempotent, so it's retried like a READ
        call, bounded by the same `max_read_retries`; once exhausted the
        clip is marked done and the failure recorded as a fact rather than
        retried forever."""
        obs = self._obs_for_job(txn.store, job.job_id)
        if obs is None:
            return
        attempts = obs.asr_attempts + 1
        exhausted = attempts > txn.store.config.max_read_retries
        txn.evidence.update_observation(obs.obs_id, asr_job_id=None, asr_attempts=attempts, asr_done=exhausted)
        if exhausted:
            txn.facts.set(
                f"asr.{obs.obs_id}.failed",
                attempts,
                FactStatus.COMMITTED,
                Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="asr.retries_exhausted",
            )

    def _obs_for_job(self, store, job_id: str):
        for obs in store.evidence.observations_by_modality("audio"):
            if obs.asr_job_id == job_id:
                return obs
        return None
