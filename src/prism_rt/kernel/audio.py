"""AsrScheduler: audio clip -> transcript segments -> ordinary text chunks.

`docs/prompt 2.txt` §9.3 and line 909. A transcript never enters the
conversation the moment transcription finishes: it is *held*
(`asr.<obs_id>.held`) and `release_due` releases held transcripts strictly
in capture order -- speech enters the conversation in the order it was
spoken, never in the order transcription happened to complete. In `auto`
mode each transcript is additionally held until `asr_dedupe_window_ms`
(800, the spec's default) after its capture; if the harness's own text
transcript covers that window, the ASR output is discarded (an
`asr.<obs_id>.disagreement` fact records it, M-01's "prefer text"). A clip
already covered by text is settled immediately -- the text *is* its
content -- so a typed utterance never waits on its own audio.

An `end_of_turn` that arrives while its utterance is still in ASR has no
turn to close yet; `reducers._apply_end_of_turn` defers it
(`asr.eot_pending`) and `_release` applies it once the transcript lands.
Untranscribed speech also counts as pending user content for the TRIAGE
hold (`interpret_apply.user_content_pending`).

All of this was found by `sim/explorer.py` (ASR scenarios A1-A4b in
`tests/explore_scenarios_mm.py`): the original implementation released on
arrival and deduped only against a turn open at that instant, which
replayed an earlier utterance after a later one, doubled an utterance
spoken in both channels, and dropped an EOT that beat its transcript --
leaving a turn open that nothing ever closed.

Two-phase, same split as `kernel/perception.py`: `decide()` dispatches
(called from `kernel/step.py`'s DECIDE phase); `on_asr_result()` holds a
resolved job's transcript and `release_due()` releases what is due
(called from the reducer and once per step, APPLY phase).
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.kernel.interpret_apply import active_goal_id, audio_covered_by_text, transcription_pending
from prism_rt.kernel.turns import TurnManager
from prism_rt.model.types import AsrSegment, FactStatus, JobKind, JobRecord, Provenance, ReadSet

_TURN_MANAGER = TurnManager()


def _eot_pending(txn) -> bool:
    fact = txn.facts.get("asr.eot_pending")
    return fact is not None and fact.status != FactStatus.RETRACTED and bool(fact.value)


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
            held = store.facts.get(f"asr.{obs.obs_id}.held")
            if held is not None and held.status != FactStatus.RETRACTED:
                continue  # transcribed, waiting for its turn to be released
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
        """Hold the transcript; `release_due` decides when (and whether) it
        enters the conversation. Released immediately would mean released
        in *completion* order -- found by `sim/explorer.py` (A2): an earlier
        clip whose first transcription attempt failed was retried and
        finished after the user's *next* clip, so "Find flights to Pune"
        was applied after "actually Mumbai"."""
        obs = self._obs_for_job(txn.store, job.job_id)
        if obs is None:
            return
        txn.evidence.update_observation(obs.obs_id, asr_job_id=None)
        if obs.asr_done:
            return  # already settled (covered by text) while this job ran
        txn.facts.set(
            f"asr.{obs.obs_id}.held",
            {
                "segments": [{"text": seg.text, "offset_us": seg.offset_us, "end_us": seg.end_us} for seg in segments],
                "end_of_utterance": bool(end_of_utterance),
            },
            FactStatus.COMMITTED,
            Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
            rule="audio.asr_held",
        )
        self.release_due(txn, now_us, step_no, event_id=event_id)

    def release_due(self, txn, now_us: int, step_no: int, *, event_id: str = "") -> None:
        """Release held transcripts strictly in capture order (called from
        every step -- a dedupe window can expire with no event arriving).

        - An earlier clip still being transcribed blocks every later one:
          speech enters the conversation in the order it was spoken.
        - AUTO mode (`docs/prompt 2.txt` line 909): a transcript is held
          until `asr_dedupe_window_ms` after its capture; if the harness's
          own text transcript covers that window, the ASR output is
          discarded (M-01's "prefer text"). The previous implementation
          released on arrival and only checked "is a turn with chunks open
          right now" -- found by the explorer to double-apply an utterance
          (A3) or replay a stale one after a later text turn (A1), leaving
          a turn open that nothing ever closed.
        """
        if not txn.store.config.asr_enabled:
            return
        audio_mode = txn.store.config.audio_mode
        window_us = txn.store.config.asr_dedupe_window_ms * 1000 if audio_mode == "auto" else 0
        for obs in sorted(txn.evidence.observations_by_modality("audio"), key=lambda o: (o.capture_ts_us, o.modality_seq)):
            if obs.asr_done:
                continue
            held = txn.facts.get(f"asr.{obs.obs_id}.held")
            covered = audio_covered_by_text(txn.store, obs)
            if (held is None or held.status == FactStatus.RETRACTED) and not covered:
                return  # still transcribing: everything after it waits
            if not covered and now_us < obs.capture_ts_us + window_us:
                txn.store.timers.schedule(f"asr_release:{obs.obs_id}", obs.capture_ts_us + window_us)
                return  # still inside its dedupe window
            txn.store.timers.cancel(f"asr_release:{obs.obs_id}")
            if held is not None and held.status != FactStatus.RETRACTED:
                txn.facts.retract(f"asr.{obs.obs_id}.held", rule="audio.asr_released")
            # Covered clips are settled now, even mid-transcription: the
            # result, whenever it lands, has nothing left to do (the job is
            # still tracked, so its eventual delivery is simply ignored).
            txn.evidence.update_observation(obs.obs_id, asr_done=True)
            if covered:
                txn.facts.set(
                    f"asr.{obs.obs_id}.disagreement",
                    True,
                    FactStatus.COMMITTED,
                    Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
                    rule="audio.asr_disagreement",
                )
                if _eot_pending(txn) and not transcription_pending(txn.store):
                    txn.facts.retract("asr.eot_pending", rule="audio.eot_covered_by_text")
                continue
            self._release(txn, obs, held.value, now_us, step_no, event_id=event_id)

    def _release(self, txn, obs, held: dict, now_us: int, step_no: int, *, event_id: str) -> None:
        gid = active_goal_id(txn.store)
        last_end_us = obs.capture_ts_us
        for seg in held.get("segments") or ():
            seg_ts = obs.capture_ts_us + int(seg.get("offset_us", 0))
            _TURN_MANAGER.on_chunk(seg["text"], event_id, seg_ts, txn, active_goal_id=gid, step_no=step_no, source="asr")
            last_end_us = max(last_end_us, obs.capture_ts_us + int(seg.get("end_us", 0)))

        if txn.store.config.audio_mode == "audio_only" and txn.store.config.asr_closes_turn and held.get("end_of_utterance"):
            turn = _TURN_MANAGER.on_eot(max(last_end_us, now_us), txn)
            _TURN_MANAGER.request_interpretation(txn, turn, now_us, step_no, event_id=event_id)
        elif _eot_pending(txn) and not transcription_pending(txn.store):
            # The harness's end_of_turn arrived while this utterance was
            # still in ASR (`reducers._apply_end_of_turn` deferred it): the
            # transcript is in now, so close the turn it belongs to.
            txn.facts.retract("asr.eot_pending", rule="audio.eot_applied")
            turn = _TURN_MANAGER.on_eot(now_us, txn)
            _TURN_MANAGER.request_interpretation(txn, turn, now_us, step_no, event_id=event_id)

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
        if obs.asr_done:
            txn.evidence.update_observation(obs.obs_id, asr_job_id=None)
            return  # already settled (covered by text) -- nothing to retry
        attempts = obs.asr_attempts + 1
        exhausted = attempts > txn.store.config.max_read_retries
        txn.evidence.update_observation(obs.obs_id, asr_job_id=None, asr_attempts=attempts, asr_done=exhausted)
        if exhausted and _eot_pending(txn) and not transcription_pending(txn.store):
            txn.facts.retract("asr.eot_pending", rule="audio.eot_dropped_no_transcript")
        if exhausted:
            self.release_due(txn, now_us, step_no, event_id=event_id)
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
