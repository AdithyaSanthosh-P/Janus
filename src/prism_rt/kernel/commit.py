"""CommitGate: admission control for tool calls, G1-G9 (Appendix B of
`docs/sonnet_implementation_plan.md`).

G1, G2, G8 apply to every call, as does G3's TRIAGE-hold clause (no new
call of any kind while a user turn spoken during an active goal awaits
interpretation, `Config.triage_hold_enabled`). The rest of G3 through G7
are write-only (§7.3: reads may be speculated and retried freely; writes
pass a single gate and are recorded in the effect ledger before emission). G9 (read-set valid *at emission*) is
enforced by EmissionGate itself, not here — this method only evaluates the
call as of the decide phase.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.lexicons import HOLD_CUES, INERT_TOKENS, TRAILING_CONNECTIVES
from prism_rt.kernel.interpret_apply import user_content_pending
from prism_rt.model.actions import IntendedAction, ToolCallBody
from prism_rt.model.types import (
    BLOCKING_EFFECT_STATUSES,
    ActionType,
    CallRecord,
    EffectRecord,
    EffectStatus,
    FactStatus,
    FloorState,
    StepKind,
    ToolMutability,
)
from prism_rt.store.session import SessionStore


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    blocked_reason: str | None
    rule_id: str


@dataclass(frozen=True)
class GateRejection:
    """P1 (docs/original_design_audit.md, "StepReport instrumentation...
    gate rejections (unblocks CS-15)"): a PROPOSED call CommitGate
    blocked this step, and why. Previously a blocked write became no
    `IntendedAction` at all, so nothing in the trace showed *that* a
    write was held back or *why* -- CS-15 ("no write emitted before
    settle_ms...") needs exactly this to be checkable offline."""

    call_id: str
    tool: str
    rule_id: str
    blocked_reason: str | None


def _turn_looks_incomplete(store: SessionStore) -> bool:
    """Day 2 (docs/fdb_v3_day2_plan.md WP1): does the latest *closed*
    user turn's own text end mid-thought ("...add two apples and",
    "...to the")? Checked against the turn's real chunk text
    (`store.turn_log`), not the `turn.<id>.prefix` fact -- that fact's
    value is a digest (read-set validity only), never the raw text.
    A turn with no chunks or empty text is never "incomplete" -- there's
    nothing to judge."""
    turns = store.turn_log.all()
    if not turns:
        return False
    turn = turns[-1]
    if not turn.chunks:
        return False
    text = " ".join(chunk.text for chunk in turn.chunks).strip()
    if not text:
        return False
    stripped = text.rstrip(" .,!?;:\"'").lower()
    if not stripped:
        return False
    for cue in HOLD_CUES:
        if stripped.endswith(cue.rstrip(",")):
            return True
    last_word = stripped.split()[-1]
    return last_word in INERT_TOKENS or last_word in TRAILING_CONNECTIVES


def _effective_settle_us(store: SessionStore) -> int:
    if store.config.incomplete_turn_settle_enabled and _turn_looks_incomplete(store):
        return store.config.settle_ms_incomplete * 1000
    return store.config.settle_ms * 1000


class CommitGate:
    def evaluate(self, call: CallRecord, store: SessionStore, now_us: int) -> GateDecision:
        # G1: tool exists and is usable
        tool = store.catalog.get(call.tool)
        if tool is None or tool.status != "USABLE":
            return GateDecision(False, "tool_not_usable", "G1")

        # G2: read set valid
        if not store.facts.is_valid(call.read_set).is_valid:
            return GateDecision(False, "read_set_invalid", "G2")

        # G8: args valid against schema
        if not store.catalog.validate_args(call.tool, call.args).valid:
            return GateDecision(False, "args_invalid", "G8")

        # TRIAGE hold, for every new tool call (`docs/prompt 2.txt` §8.3:
        # "new tool calls, speech, clarifications, and final responses
        # produced during triage go to ProposalHold"; G3's "task state is
        # not TRIAGE"; CS-15's "interpretation pending"): a user turn has
        # closed but isn't interpreted yet, and may be a correction. The
        # call just stays PROPOSED -- re-evaluated every step like any
        # blocked call -- and is released if its read set survives the
        # interpretation, or discarded by ordinary invalidation if not.
        # Found by `sim/explorer.py`: with INTERPRET slower than settle_ms
        # (normal for a live model), a write for a value the user had
        # already corrected passed every other gate; and I-01's contract
        # (a superseded first utterance never produces a call) needs reads
        # held too once the rapid follow-up is queued rather than dropped.
        if store.config.triage_hold_enabled and user_content_pending(store):
            return GateDecision(False, "interpretation_pending", "G3")

        if call.kind != StepKind.WRITE:
            if store.config.settle_reads_enabled:
                # Config.settle_reads_enabled: a read waits out the same
                # floor + settle barrier as a write, so a turn closed early
                # by a mid-sentence pause can't fire a call the rest of the
                # sentence would change.
                if store.floor_state != FloorState.USER_TURN_CLOSED:
                    return GateDecision(False, "floor_open", "G3")
                settle = self._settle_block(store, now_us)
                if settle is not None:
                    return settle
            return GateDecision(True, None, "G1,G2,G8")

        # --- write-only conditions ------------------------------------

        # G_WATCHDOG (Phase A, docs/prompt 2.txt §8.9): once the scenario
        # watchdog has fired, no new write is ever admitted — the harness's
        # wall-clock budget is spent. Reads may still complete; only writes
        # are barred, matching §8.9's "do not emit new writes" exactly.
        watchdog_fact = store.facts.get("session.watchdog_fired")
        if watchdog_fact is not None and watchdog_fact.status != FactStatus.RETRACTED and watchdog_fact.value:
            return GateDecision(False, "watchdog_fired", "G_WATCHDOG")

        # G3: floor closed (no commit while the user may still be correcting)
        if store.floor_state != FloorState.USER_TURN_CLOSED:
            return GateDecision(False, "floor_open", "G3")
        # G4: commit_intent true for the call's goal. Day 2 (§5.2,
        # Config.g4_exempt_undeclared_mutability's own docstring): a tool
        # whose mutability was never declared (DEFAULTED to STATE_CHANGING,
        # the catalog's safe default -- e.g. every FDB-v3 tool) doesn't
        # need an *explicit* commit_intent when this flag is on; G3 (floor
        # closed, already checked above) and G10/G11 (settle, checked
        # below) still gate it. A tool whose mutability *was* declared is
        # unaffected either way -- this never weakens a real write.
        undeclared_exempt = store.config.g4_exempt_undeclared_mutability and tool.mutability_source == "DEFAULTED"
        if not undeclared_exempt:
            intent_fact = store.facts.get(f"goal.{call.goal_id}.commit_intent")
            if intent_fact is None or intent_fact.status == FactStatus.RETRACTED or intent_fact.value is not True:
                return GateDecision(False, "commit_intent_false", "G4")

        # G5: no existing effect with the same fingerprint (non-FAILED)
        existing_fp = store.effect_ledger.by_fingerprint(call.fingerprint)
        if existing_fp is not None and existing_fp.status in BLOCKING_EFFECT_STATUSES:
            return GateDecision(False, "duplicate_fingerprint", "G5")

        # G6: no existing effect with the same lineage, PENDING or CONFIRMED
        lineage = f"{call.goal_id}:{call.step_key}"
        existing_lineage = store.effect_ledger.by_lineage(lineage)
        if existing_lineage is not None and existing_lineage.status in (
            EffectStatus.PENDING,
            EffectStatus.CONFIRMED,
        ):
            return GateDecision(False, "duplicate_lineage", "G6")

        # G7: no UNKNOWN effect for the same lineage (must reconcile first)
        if existing_lineage is not None and existing_lineage.status == EffectStatus.UNKNOWN:
            return GateDecision(False, "unknown_effect_outcome", "G7")

        # G10/G11: settle barrier (V2). Catches "book the 6pm one" -> 200ms
        # -> "wait, the 8pm one" — without this, the first booking would
        # already be in flight by the time the correction arrives. A write
        # waits until config.settle_ms has passed since the floor last
        # closed, AND no newer turn has opened since (a still-open floor
        # already fails G3 above, but a turn can open and close again
        # within the settle window itself, which G3 alone wouldn't catch).
        settle = self._settle_block(store, now_us)
        if settle is not None:
            return settle

        return GateDecision(True, None, "G1-G8")

    def _settle_block(self, store: SessionStore, now_us: int) -> GateDecision | None:
        if not store.config.settle_barrier_enabled:
            return None
        last_eot = store.facts.get("session.last_eot_ts")
        last_eot_ts = last_eot.value if last_eot is not None and last_eot.status != FactStatus.RETRACTED else None
        settle_us = _effective_settle_us(store)
        if last_eot_ts is None or (now_us - last_eot_ts) < settle_us:
            return GateDecision(False, "settle_not_elapsed", "G10")
        if store.turn_log.open_turn_id() is not None:
            return GateDecision(False, "new_turn_open", "G11")
        return None

    def scan_and_admit(
        self, store: SessionStore, now_us: int
    ) -> tuple[list[IntendedAction], set[str], list[GateRejection]]:
        """Evaluate every PROPOSED call and turn the admitted ones into
        TOOL_CALL IntendedActions. A call that fails the gate simply stays
        PROPOSED — there is no separate Blocked status; it is re-evaluated
        on the next step once whatever blocked it may have changed.

        Also returns the admitted call_ids: a call admitted just now stays
        CallStatus.PROPOSED until EmissionGate flips it to IN_FLIGHT later
        this same step, so anything else scanning `proposed()` calls this
        step (V1: FastResponder's blocked-write check) needs to exclude
        them — otherwise a write's own brand-new PENDING effect record
        looks, to a second independent CommitGate.evaluate() call, like a
        pre-existing duplicate of itself.

        P1 (docs/original_design_audit.md): also returns every rejection
        this step, not just the admissions — `kernel/step.py` surfaces
        these on `StepReport.gate_rejections`, unblocking CS-15 (a
        blocked write previously left no trace at all)."""
        actions: list[IntendedAction] = []
        admitted_call_ids: set[str] = set()
        rejections: list[GateRejection] = []
        for call in store.call_ledger.proposed():
            decision = self.evaluate(call, store, now_us)
            if not decision.allowed:
                rejections.append(GateRejection(call.call_id, call.tool, decision.rule_id, decision.blocked_reason))
                if decision.rule_id == "G10":
                    self._schedule_settle_wake(store, call, now_us)
                continue
            admitted_call_ids.add(call.call_id)
            store.timers.cancel(f"settle:{call.call_id}")

            if call.kind == StepKind.WRITE:
                lineage = f"{call.goal_id}:{call.step_key}"
                store.effect_ledger.create(
                    EffectRecord(
                        fingerprint=call.fingerprint,
                        lineage=lineage,
                        call_id=call.call_id,
                        status=EffectStatus.PENDING,
                    )
                )

            mutability = (
                ToolMutability.STATE_CHANGING if call.kind == StepKind.WRITE else ToolMutability.READ_ONLY
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.TOOL_CALL,
                    body=ToolCallBody(
                        call_id=call.call_id,
                        tool_name=call.tool,
                        arguments=call.args,
                        mutability=mutability,
                    ),
                    read_set=call.read_set,
                    rule_id=decision.rule_id,
                )
            )
        return actions, admitted_call_ids, rejections

    def _schedule_settle_wake(self, store: SessionStore, call: CallRecord, now_us: int) -> None:
        """A liveness nudge, not a correctness mechanism (T-05) — G10 is
        re-checked from scratch every step regardless of whether this timer
        ever fires; it only tells the driver the earliest time it must call
        step() again even with no new event, so the settle window can't
        stall forever under Model B (docs/prompt 2.txt §13.3). P0.1
        (docs/original_design_audit.md, D1): `entry.py` now actually
        honours this — Model A's CoupledClock (`adapters/clock.py`)
        derives real elapsed time on its own, and Model B's idle loop jumps
        straight to `StepReport.next_wake_us` — so this timer no longer
        goes unread in production, which is all D1 was ever about."""
        last_eot = store.facts.get("session.last_eot_ts")
        last_eot_ts = last_eot.value if last_eot is not None and last_eot.status != FactStatus.RETRACTED else now_us
        due_us = last_eot_ts + _effective_settle_us(store)
        store.timers.schedule(f"settle:{call.call_id}", due_us)
