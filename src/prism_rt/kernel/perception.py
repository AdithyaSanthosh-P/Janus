"""PerceptionScheduler: V3 multimodal question lifecycle.

`docs/prompt 2.txt` §10, `docs/theme05_implementation_blueprint.md` §9 —
simplified per `docs/sonnet_implementation_plan.md`'s V3 scope (see
`model/types.py`'s V3 section docstring for exactly what's cut). In
particular: only the "demand" question source is implemented (an
Interpreter's `visual_reference` on an accepted interpretation) — the "cue"
source (QuickDetector's `visual_reference` signal creating a speculative
question before end-of-turn) and the "plan" source (a step declaring an
unbound `visual_candidate` parameter) are not wired up. Everything a
question needs once it exists — frame alignment, dispatch, claim
acceptance, conflicts, lease expiry — is real.

All four public methods are no-ops when `config.vision_enabled` is False,
so V0-V2 (which never set it) see zero behavior change from this module
existing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prism_rt.model.types import (
    Conflict,
    ConflictStatus,
    Confidence,
    FactStatus,
    GoalStatus,
    JobKind,
    JobRecord,
    Observation,
    PerceptionClaim,
    Provenance,
    Question,
    QuestionMode,
    QuestionStatus,
    QuestionTarget,
    ReadSet,
)


@dataclass(frozen=True)
class VisionDispatchRequest:
    """Same shape as `kernel.task.DispatchRequest` (duck-typed by
    `Kernel.step`'s DISPATCH phase — it only reads `.job_id`/`.kind`/`.view`)
    — a separate class rather than importing that one to avoid a
    perception -> task -> interpret_apply -> perception import cycle
    (`kernel/task.py` and `kernel/interpret_apply.py` already depend on each
    other one level down)."""

    job_id: str
    kind: JobKind
    view: dict
    goal_id: str | None
    turn_id: str | None
    read_set: ReadSet


def _goal_active(store, goal_id: str) -> bool:
    goal = store.goals.get(goal_id)
    return goal is not None and goal.status == GoalStatus.ACTIVE


_GENERIC_TARGETS = (
    QuestionTarget(name="device_model", description="the device's model or identity"),
    QuestionTarget(name="visible_state", description="salient visible state (e.g. an indicator light)"),
)


class PerceptionScheduler:
    # --- question lifecycle (called from kernel/interpret_apply.py, APPLY phase) ---

    def create_or_retarget_question(
        self,
        txn,
        goal_id: str,
        candidates,
        mode: str,
        *,
        anchor_ts_us: int,
        created_by: str,
    ) -> str:
        if not txn.store.config.vision_enabled:
            return ""
        targets = tuple(QuestionTarget(name=c.name, description=c.description) for c in candidates) or _GENERIC_TARGETS
        question_mode = QuestionMode(mode) if mode in (m.value for m in QuestionMode) else QuestionMode.AT_UTTERANCE

        existing = txn.evidence.latest_active_question(goal_id)
        if existing is not None:
            # The user has asked for a fresh look ("actually, the other
            # one"): whatever perception concluded for this question before
            # is superseded evidence. Retract it through the ordinary path
            # -- claim facts (the question id is reused, so `$Q` bindings
            # would otherwise keep reading them) and perception-sourced slot
            # values -- so same-step invalidation cancels or discards
            # anything built on it, and `_perception_will_answer` waits for
            # the new analysis instead of the old answer carrying the goal
            # to a FINAL. User-stated values are never touched. Same shape
            # as `expire_leases`' renewal. Found by `sim/explorer.py`
            # (scenarios V1/V2): the FINAL was grounded in the first frame's
            # colour after the user redirected to the second.
            names = {t.name for t in existing.targets} | {t.name for t in targets}
            # A clarification already pending about one of these targets is
            # answered by this re-look, not by the user: found by the
            # explorer (V3) -- a CLARIFY queued after the first frame's
            # analysis failed was released right after the user redirected
            # to a new frame perception was about to analyze.
            clarify = txn.facts.get(f"goal.{goal_id}.clarify_target")
            if clarify is not None and clarify.status != FactStatus.RETRACTED and clarify.value in (
                {f"slot.{goal_id}.{n}" for n in names} | {f"claim.{existing.question_id}.{n}" for n in names}
            ):
                txn.facts.retract(f"goal.{goal_id}.clarify_target", rule="perception.retarget_clarify_resolved")
            for name in names:
                claim_key = f"claim.{existing.question_id}.{name}"
                claim = txn.facts.get(claim_key)
                if claim is not None and claim.status != FactStatus.RETRACTED:
                    txn.facts.retract(claim_key, rule="perception.retarget")
                slot_key = f"slot.{goal_id}.{name}"
                slot = txn.facts.get(slot_key)
                if slot is not None and slot.status != FactStatus.RETRACTED and slot.provenance.source == "perception":
                    txn.facts.retract(slot_key, rule="perception.retarget")
            txn.evidence.update_question(
                existing.question_id,
                targets=targets,
                mode=question_mode,
                anchor_ts_us=anchor_ts_us,
                status=QuestionStatus.OPEN,
                pending_job_id=None,
                pending_obs_id=None,
                analyzed_obs_id=None,
            )
            return existing.question_id

        qid = txn.store.ids.next("question")
        txn.evidence.create_question(
            Question(
                question_id=qid,
                goal_id=goal_id,
                targets=targets,
                mode=question_mode,
                anchor_ts_us=anchor_ts_us,
                created_by=created_by,
            )
        )
        return qid

    # --- lease expiry (called from kernel/step.py, inside the txn, every step) ---

    def expire_leases(self, txn, now_us: int, step_no: int) -> None:
        if not txn.store.config.vision_enabled:
            return
        lease_us = txn.store.config.evidence_lease_ms * 1000
        for question in txn.evidence.all_questions():
            if question.mode != QuestionMode.CURRENT_STATE or question.status != QuestionStatus.ANSWERED:
                continue
            if not _goal_active(txn.store, question.goal_id):
                continue  # the conversation moved on; don't renew (and re-pay for) evidence nobody needs
            expired = False
            for target in question.targets:
                claim_key = f"claim.{question.question_id}.{target.name}"
                fact = txn.facts.get(claim_key)
                if fact is None or fact.status == FactStatus.RETRACTED:
                    continue
                if now_us - fact.provenance.ts_us < lease_us:
                    continue
                txn.facts.retract(claim_key, rule="perception.lease_expired")
                expired = True
                slot_key = f"slot.{question.goal_id}.{target.name}"
                slot_fact = txn.facts.get(slot_key)
                if slot_fact is not None and slot_fact.provenance.source == "perception":
                    txn.facts.retract(slot_key, rule="perception.lease_expired")
            if expired:
                txn.evidence.update_question(
                    question.question_id,
                    status=QuestionStatus.OPEN,
                    created_by="renewal",
                    pending_job_id=None,
                    pending_obs_id=None,
                    analyzed_obs_id=None,
                )

    # --- dispatch (called from kernel/step.py, DECIDE phase) ---

    def decide(self, store, now_us: int, step_no: int) -> list[VisionDispatchRequest]:
        if not store.config.vision_enabled:
            return []
        requests: list[DispatchRequest] = []
        for question in store.evidence.all_questions():
            if question.status != QuestionStatus.OPEN or question.pending_job_id is not None:
                continue
            if not _goal_active(store, question.goal_id):
                # Found by `sim/explorer.py` (V2): a CURRENT_STATE question
                # kept re-analyzing on every lease renewal long after its
                # goal had COMPLETED -- a paid vision call every
                # `evidence_lease_ms`, forever.
                continue
            obs = self._select_frame(store, question)
            if obs is None:
                continue  # still waiting for an aligned frame
            if obs.obs_id == question.analyzed_obs_id:
                continue  # already analyzed without a full answer; wait for new evidence

            job_id = store.ids.next("job")
            read_set = store.facts.build_read_set(["goal.active"])
            view = {
                "obs_id": obs.obs_id,
                "frame_id": obs.frame_id,  # harness-given reference — see workers/vision.py's module docstring
                "mode": question.mode.value,
                "targets": [{"name": t.name, "description": t.description} for t in question.targets],
            }
            store.jobs.create(
                JobRecord(job_id=job_id, kind=JobKind.VISION, goal_id=question.goal_id, turn_id=None, read_set=read_set)
            )
            store.evidence.update_question(question.question_id, pending_job_id=job_id, pending_obs_id=obs.obs_id)
            requests.append(
                VisionDispatchRequest(
                    job_id=job_id, kind=JobKind.VISION, view=view, goal_id=question.goal_id, turn_id=None, read_set=read_set
                )
            )
        return requests

    def _select_frame(self, store, question: Question) -> Observation | None:
        # Phase 2: observations() now also holds audio clips — vision must
        # only ever pick a video frame, never an audio observation.
        obs_list = store.evidence.observations_by_modality("frame")
        if not obs_list:
            return None
        if question.mode == QuestionMode.CURRENT_STATE:
            return obs_list[-1]

        anchor = question.anchor_ts_us
        # Ties on capture time are broken by arrival order (modality_seq):
        # when two frames share a timestamp, the later arrival is the more
        # recent evidence -- arrival order is the only recency information
        # left. `max` on capture time alone returned the *first* (older)
        # frame of a tie; found by `sim/explorer.py` (V1/V3/V4 with both
        # frames compressed onto one timestamp): "actually, the other one"
        # re-selected the very frame the user had redirected away from.
        at_or_before = [o for o in obs_list if o.capture_ts_us <= anchor]
        if at_or_before:
            return max(at_or_before, key=lambda o: (o.capture_ts_us, o.modality_seq))
        window_us = store.config.evidence_align_window_ms * 1000
        after = [o for o in obs_list if anchor < o.capture_ts_us <= anchor + window_us]
        return min(after, key=lambda o: (o.capture_ts_us, o.modality_seq)) if after else None

    def release_dropped_job(self, txn, job) -> None:
        """A VISION job that errored or went stale never reaches
        `on_perception_result`, so without this its question kept
        `pending_job_id` set forever and `decide` never analyzed it again,
        even after a new frame arrived. The frame counts as analyzed (the
        provider already retries transient errors itself), so a persistently
        failing provider can't hot-loop; new evidence, a retarget, or a
        lease renewal re-opens analysis."""
        question = txn.evidence.question_by_pending_job(job.job_id)
        if question is None:
            return
        txn.evidence.update_question(
            question.question_id,
            pending_job_id=None,
            pending_obs_id=None,
            analyzed_obs_id=question.pending_obs_id,
        )

    # --- claim acceptance (called from kernel/reducers.py, APPLY phase) ---

    def on_perception_result(
        self, txn, job, claims: tuple[PerceptionClaim, ...], now_us: int, step_no: int, *, event_id: str
    ) -> None:
        question = txn.evidence.question_by_pending_job(job.job_id)
        if question is None:
            return
        obs_id = question.pending_obs_id or ""
        txn.evidence.update_question(
            question.question_id, pending_job_id=None, pending_obs_id=None, analyzed_obs_id=obs_id or None
        )

        target_names = {t.name for t in question.targets}
        accepted_names: set[str] = set()
        for claim in claims:
            if claim.name not in target_names or claim.confidence == Confidence.LOW:
                continue  # non-target claims dropped; LOW never accepted as a fact
            accepted_names.add(claim.name)
            txn.facts.set(
                f"claim.{question.question_id}.{claim.name}",
                claim.value,
                FactStatus.DERIVED,
                Provenance(source="perception", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="perception.claim",
            )
            if claim.confidence != Confidence.HIGH:
                continue  # MEDIUM: claim fact only, no slot write, no conflict check

            slot_key = f"slot.{question.goal_id}.{claim.name}"
            existing_slot = txn.facts.get(slot_key)
            user_has_value = (
                existing_slot is not None
                and existing_slot.status not in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS)
                and existing_slot.provenance.source == "user"
            )
            if not user_has_value:
                # A compiled plan keeps the user's value under the action's own
                # key (slot.<g>.a0.<name>); it outranks the camera just the same.
                action_value = re.compile(rf"^slot\.{re.escape(question.goal_id)}\.a\d+\.{re.escape(claim.name)}$")
                for key, fact in txn.store.facts.by_prefix(f"slot.{question.goal_id}.a").items():
                    if (
                        action_value.match(key)
                        and fact.status not in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS)
                        and fact.provenance.source == "user"
                    ):
                        existing_slot, user_has_value = fact, True
                        break
            if user_has_value and existing_slot.value != claim.value:
                self._open_conflict(
                    txn, question.goal_id, claim.name, existing_slot.value, claim.value, obs_id=obs_id, now_us=now_us
                )
                continue  # never overwrite the user's stated value (§9.1 rank 1 > rank 3)
            txn.facts.set(
                slot_key,
                claim.value,
                FactStatus.DERIVED,
                Provenance(source="perception", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="perception.claim_slot",
            )
            # The goal may already be CLARIFYING on exactly this slot (an
            # earlier analysis came back LOW, or a VISION job failed) --
            # perception has now answered it, same as a user answer would
            # (`interpret_apply`'s clarify_resolved retraction).
            clarify = txn.facts.get(f"goal.{question.goal_id}.clarify_target")
            answered_keys = (slot_key, f"claim.{question.question_id}.{claim.name}")
            # A compiled goal asks on the per-action key (slot.<g>.a0.<name>);
            # the executor binds this plain slot when the action has no value
            # of its own, so the camera answers that question too.
            per_action = re.fullmatch(rf"slot\.{re.escape(question.goal_id)}\.a\d+\.{re.escape(claim.name)}", str(clarify.value)) if clarify is not None else None
            if clarify is not None and clarify.status != FactStatus.RETRACTED and (clarify.value in answered_keys or per_action):
                txn.facts.retract(f"goal.{question.goal_id}.clarify_target", rule="perception.clarify_resolved")
            fresh = txn.facts.get(f"perception.{question.goal_id}.fresh_view_for")
            if fresh is not None and fresh.status != FactStatus.RETRACTED and fresh.value in answered_keys:
                txn.facts.retract(f"perception.{question.goal_id}.fresh_view_for", rule="perception.fresh_view_received")

        if target_names and target_names <= accepted_names and question.status == QuestionStatus.OPEN:
            txn.evidence.update_question(question.question_id, status=QuestionStatus.ANSWERED)

    def _open_conflict(self, txn, goal_id: str, name: str, user_value, claim_value, *, obs_id: str, now_us: int) -> None:
        if txn.evidence.conflict_open(goal_id, name):
            return
        conflict_id = txn.store.ids.next("conflict")
        txn.evidence.create_conflict(
            Conflict(
                conflict_id=conflict_id,
                goal_id=goal_id,
                name=name,
                candidates=(
                    {"value": user_value, "source": "user", "confidence": None, "ref": None},
                    {"value": claim_value, "source": "perception", "confidence": "high", "ref": obs_id},
                ),
                status=ConflictStatus.OPEN,
            )
        )
