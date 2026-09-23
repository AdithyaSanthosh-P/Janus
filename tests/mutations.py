"""Controlled safeguard-disable ("mutation") experiments for the explorer.

Each entry disables exactly one safeguard, test-locally via
`unittest.mock.patch` (production code and defaults are untouched), and
names the scenarios whose races that safeguard exists to prevent. With the
safeguard in place those scenarios explore clean; with it removed, the
explorer must find violations. That is the evidence that each safeguard
matters -- not merely that the final code passes.
"""

from __future__ import annotations

import contextlib
from unittest import mock

from explore_scenarios import SEARCH_CORRECTION, WRITE_CORRECTION
from explore_scenarios_mm import (
    A1_ASR_VS_TEXT_CORRECTION,
    A2_EARLIER_VS_NEWER_SPEECH,
    A3_ASR_VS_EOT,
    A4B_MALFORMED_ASR_THEN_TEXT,
    V4_SLOW_VISION_WATCHDOG,
    V1_VISION_REDIRECT,
    V2_NEW_FRAME_VS_OLD_RESULT,
    V3_VISION_FAILURE_VS_REDIRECT,
)

import prism_rt.kernel.interpret_apply as interpret_apply
import prism_rt.kernel.reducers as reducers
import prism_rt.kernel.perception as perception
import prism_rt.kernel.responder as responder
import prism_rt.kernel.task as task
from prism_rt.kernel.audio import AsrScheduler
from prism_rt.model.types import FactStatus, QuestionMode
from prism_rt.sim.explorer import with_config
from prism_rt.store.facts import FactStore
from prism_rt.store.ledgers import EvidenceStore


def _unground(store, goal_id):
    """Pre-fix behavior: any non-retracted compose text is usable."""
    fact = store.facts.get(f"compose.{goal_id}.text")
    return None if fact is None or fact.status == FactStatus.RETRACTED else fact


@contextlib.contextmanager
def compose_grounding_off():
    with mock.patch.object(task, "grounded_compose_text", _unground), mock.patch.object(responder, "grounded_compose_text", _unground):
        yield


_real_retract = FactStore.retract


def _skip_retraction(rule_to_skip: str):
    def retract(self, key, *, rule, provenance=None):
        if rule == rule_to_skip:
            return False
        return _real_retract(self, key, rule=rule, provenance=provenance)

    return retract


@contextlib.contextmanager
def retarget_retraction_off():
    with mock.patch.object(FactStore, "retract", _skip_retraction("perception.retarget")):
        yield


@contextlib.contextmanager
def retarget_clears_clarify_off():
    with mock.patch.object(FactStore, "retract", _skip_retraction("perception.retarget_clarify_resolved")):
        yield


@contextlib.contextmanager
def watchdog_exemption_too_broad():
    """Pre-fix behavior: once the watchdog fired, *any* FINAL bypassed the
    TRIAGE hold, not only the salvage FINAL."""
    def fired(store):
        fact = store.facts.get("session.watchdog_fired")
        return fact is not None and fact.status != FactStatus.RETRACTED and bool(fact.value)

    with mock.patch.object(responder, "_is_watchdog_salvage", fired):
        yield


@contextlib.contextmanager
def deferred_eot_off():
    """Pre-fix behavior: an end_of_turn arriving while the utterance is
    still in ASR is dropped instead of deferred."""
    with mock.patch.object(reducers, "transcription_pending", lambda store: False):
        yield


@contextlib.contextmanager
def malformed_asr_not_a_failure():
    """Pre-fix behavior: a malformed transcript was silently ignored,
    leaving the clip's job id set forever, instead of counting as a failed
    attempt. Only the malformed path is reverted -- genuine errors still
    go through the normal retry path."""
    state = {"malformed": False}
    real_parse, real_release = reducers.parse_asr, AsrScheduler.release_dropped_job

    def parse(raw):
        try:
            return real_parse(raw)
        except (KeyError, ValueError):
            state["malformed"] = True
            raise

    def release(self, txn, job, now_us, step_no, *, event_id):
        if state["malformed"]:
            state["malformed"] = False
            return None
        return real_release(self, txn, job, now_us, step_no, event_id=event_id)

    with mock.patch.object(reducers, "parse_asr", parse), mock.patch.object(AsrScheduler, "release_dropped_job", release):
        yield


@contextlib.contextmanager
def responding_revert_off():
    with mock.patch.object(task.TaskStateMachine, "_consumed_step_went_stale", lambda self, store, gid: False):
        yield


@contextlib.contextmanager
def vision_job_identity_off():
    """A late vision result is applied to whatever question is current,
    instead of only to the question that dispatched it."""
    with mock.patch.object(EvidenceStore, "question_by_pending_job", lambda self, job_id: (self.all_questions() or [None])[-1]):
        yield


def _old_select_frame(self, store, question):
    obs_list = store.evidence.observations_by_modality("frame")
    if not obs_list:
        return None
    if question.mode == QuestionMode.CURRENT_STATE:
        return obs_list[-1]
    at_or_before = [o for o in obs_list if o.capture_ts_us <= question.anchor_ts_us]
    if at_or_before:
        return max(at_or_before, key=lambda o: o.capture_ts_us)
    return None


@contextlib.contextmanager
def frame_tiebreak_off():
    with mock.patch.object(perception.PerceptionScheduler, "_select_frame", _old_select_frame):
        yield


@contextlib.contextmanager
def perception_goal_gate_off():
    with mock.patch.object(perception, "_goal_active", lambda store, goal_id: True):
        yield


def _immediate_release(self, txn, job, segments, end_of_utterance, now_us, step_no, *, event_id):
    """Pre-fix behavior: a transcript enters the conversation the moment
    transcription finishes -- no capture order, no dedupe window."""
    obs = self._obs_for_job(txn.store, job.job_id)
    if obs is None:
        return
    txn.evidence.update_observation(obs.obs_id, asr_job_id=None, asr_done=True)
    held = {"segments": [{"text": s.text, "offset_us": s.offset_us, "end_us": s.end_us} for s in segments], "end_of_utterance": end_of_utterance}
    self._release(txn, obs, held, now_us, step_no, event_id=event_id)


@contextlib.contextmanager
def asr_ordered_release_off():
    with mock.patch.object(AsrScheduler, "on_asr_result", _immediate_release):
        yield


@contextlib.contextmanager
def asr_hold_off():
    """Untranscribed speech no longer counts as pending user content."""
    with mock.patch.object(interpret_apply, "transcription_pending", lambda store: False):
        yield


@contextlib.contextmanager
def nothing():
    yield


# name -> (context manager, scenarios the safeguard protects, scenario transform)
MUTATIONS = {
    "triage_hold (text)": (nothing, (SEARCH_CORRECTION, WRITE_CORRECTION), lambda s: with_config(s, triage_hold_enabled=False)),
    "triage_hold (multimodal)": (nothing, (V1_VISION_REDIRECT, A2_EARLIER_VS_NEWER_SPEECH), lambda s: with_config(s, triage_hold_enabled=False)),
    "compose_text_grounding": (compose_grounding_off, (SEARCH_CORRECTION,), lambda s: s),
    "retarget_retracts_old_evidence": (retarget_retraction_off, (V1_VISION_REDIRECT, V2_NEW_FRAME_VS_OLD_RESULT), lambda s: s),
    "responding_rechecks_step_validity": (responding_revert_off, (V1_VISION_REDIRECT, V2_NEW_FRAME_VS_OLD_RESULT), lambda s: s),
    "vision_job_identity": (vision_job_identity_off, (V1_VISION_REDIRECT, V3_VISION_FAILURE_VS_REDIRECT), lambda s: s),
    "frame_tiebreak_by_arrival": (frame_tiebreak_off, (V1_VISION_REDIRECT, V3_VISION_FAILURE_VS_REDIRECT), lambda s: s),
    "perception_stops_after_goal": (perception_goal_gate_off, (V2_NEW_FRAME_VS_OLD_RESULT,), lambda s: s),
    "asr_capture_order_and_dedupe_window": (asr_ordered_release_off, (A2_EARLIER_VS_NEWER_SPEECH, A3_ASR_VS_EOT), lambda s: s),
    "asr_counts_as_pending_user_content": (asr_hold_off, (A2_EARLIER_VS_NEWER_SPEECH,), lambda s: s),
    "retarget_clears_pending_clarify": (retarget_clears_clarify_off, (V3_VISION_FAILURE_VS_REDIRECT,), lambda s: s),
    "watchdog_exemption_only_for_salvage": (watchdog_exemption_too_broad, (V4_SLOW_VISION_WATCHDOG,), lambda s: s),
    "deferred_end_of_turn": (deferred_eot_off, (A1_ASR_VS_TEXT_CORRECTION,), lambda s: s),
    "malformed_asr_counts_as_failure": (malformed_asr_not_a_failure, (A4B_MALFORMED_ASR_THEN_TEXT,), lambda s: s),
}
