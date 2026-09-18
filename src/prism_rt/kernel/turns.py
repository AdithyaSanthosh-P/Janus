"""TurnManager: turn assembly, chunk buffering, prefix digest.

The prefix digest is a real fact (`turn.<turn_id>.prefix`, Appendix A of
`docs/sonnet_implementation_plan.md`) — not just bookkeeping. An INTERPRET
job's read set includes it, so a chunk arriving while interpretation is in
flight invalidates that job automatically through the same read-set
mechanism as everything else, with no special-casing.
"""

from __future__ import annotations

from prism_rt.canonical import compute_digest, normalize_value
from prism_rt.kernel.detector import QuickDetector
from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.model.types import FactStatus, FloorState, GoalStatus, Provenance, TaskState, Turn
from prism_rt.store.session import StoreTxn


def _tool_params(store) -> tuple[tuple[str, dict], ...]:
    """`[(param_name, json_schema), ...]` across every usable tool —
    QuickDetector's enum-match extractor input. Not narrowed to "tools
    relevant to the active goal" (§9.2's own phrasing) — a documented
    scope trim; this project's toy catalogs are small enough that the
    false-positive risk is negligible, and narrowing would need plan/goal
    plumbing this call site doesn't otherwise need."""
    params: list[tuple[str, dict]] = []
    for tool in store.catalog.usable_tools():
        props = (tool.params_schema or {}).get("properties", {})
        for name, schema in props.items():
            params.append((name, schema))
    return tuple(params)


def _seen_values(store) -> dict[str, set[str]]:
    """`{slot_name: {values...}}` from every committed `slot.*.<name>`
    fact this session — QuickDetector's session-entity extractor input."""
    seen: dict[str, set[str]] = {}
    for key, value in store.facts.snapshot_committed().items():
        if not key.startswith("slot.") or not isinstance(value, str):
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        seen.setdefault(parts[2], set()).add(value)
    return seen


class TurnManager:
    def __init__(self) -> None:
        self._quick_detector = QuickDetector()

    def on_chunk(
        self,
        text: str,
        event_id: str,
        ts_us: int,
        txn: StoreTxn,
        *,
        active_goal_id: str | None,
        step_no: int = 0,
    ) -> Turn:
        turn_id = txn.turn_log.open_turn_id()
        if turn_id is None:
            turn_id = txn.store.ids.next("turn")
            txn.turn_log.open_turn(
                turn_id, ts_us, is_interruption=active_goal_id is not None, during_goal=active_goal_id
            )
            txn.set_floor(FloorState.USER_TURN_OPEN)

        turn = txn.turn_log.append_chunk(turn_id, ts_us, text)
        prefix_text = " ".join(chunk.text for chunk in turn.chunks)
        digest = compute_digest(prefix_text)
        txn.facts.set(
            f"turn.{turn_id}.prefix",
            digest,
            FactStatus.COMMITTED,
            Provenance(source="user", event_id=event_id, turn_id=turn_id, ts_us=ts_us),
            rule="turns.chunk",
        )
        self._detect_chunk_anchor(text, turn, txn, active_goal_id, event_id, ts_us, step_no)
        return turn

    def _detect_chunk_anchor(
        self,
        text: str,
        turn: Turn,
        txn: StoreTxn,
        gid: str | None,
        event_id: str,
        ts_us: int,
        step_no: int,
    ) -> None:
        """Phase 4 (`docs/post_v4_implementation_plan.md`): the CHUNK
        cancellation anchor (`docs/prompt 2.txt` §8.2). Every HIGH-confidence
        value QuickDetector finds is recorded as a `hyp.<turn>.<name>` fact
        (§9.2) regardless of context. Only a value that both differs from
        an existing `slot.<gid>.<name>` and occurs in an interruption,
        active-goal, or correction-cue context (§8.2's precise chunk-anchor
        rule) is fed to `StoreTxn.mark_chunk_anchor` — which cancels
        whatever in-flight call currently depends on that slot, in this
        same step, without ever touching the slot's own committed value."""
        values = self._quick_detector.detect_values(
            text, tool_params=_tool_params(txn.store), seen_values=_seen_values(txn.store)
        )
        if not values:
            return

        goal = txn.store.goals.get(gid) if gid is not None else None
        goal_active = goal is not None and goal.status == GoalStatus.ACTIVE
        anchor_context = turn.is_interruption or goal_active or self._quick_detector.detect(text).is_correction

        for hv in values:
            txn.facts.set(
                f"hyp.{turn.turn_id}.{hv.slot_name}",
                hv.value,
                FactStatus.HYPOTHESIS,
                Provenance(source="system", event_id=event_id, turn_id=turn.turn_id, step_no=step_no, ts_us=ts_us),
                rule="detector.hypothesis",
            )
            if gid is None or not anchor_context:
                continue
            slot_key = f"slot.{gid}.{hv.slot_name}"
            candidate_digest = compute_digest(normalize_value(hv.value))
            existing = txn.facts.get(slot_key)
            if existing is not None and existing.status != FactStatus.RETRACTED and existing.digest == candidate_digest:
                continue  # already matches — nothing to correct
            txn.mark_chunk_anchor(slot_key)

    def on_eot(self, ts_us: int, txn: StoreTxn) -> Turn | None:
        turn_id = txn.turn_log.open_turn_id()
        if turn_id is None:
            return None
        turn = txn.turn_log.close_turn(turn_id, ts_us)
        txn.set_floor(FloorState.USER_TURN_CLOSED)
        return turn

    def request_interpretation(self, txn: StoreTxn, turn: Turn | None, now_us: int, step_no: int, *, event_id: str) -> None:
        """The rest of what closing a turn means: mark the settle-barrier
        timestamp and (unless it's a pure backchannel during ongoing work,
        I-11) request an INTERPRET dispatch. Split out from `on_eot` so
        both the ordinary `end_of_turn` reducer and `kernel/audio.py`'s
        ASR-closes-turn path (Phase 2, `docs/post_v4_implementation_plan.md`)
        go through the exact same logic — a turn closed by a synthesized
        end-of-utterance must trigger interpretation exactly like one
        closed by a real `end_of_turn` event, not a parallel, easy-to-drift
        reimplementation of "what happens next"."""
        if turn is None or not turn.chunks:
            return

        # V2: settle barrier needs to know when the floor last closed,
        # regardless of whether this turn dispatches an interpretation
        # (kernel/commit.py G10).
        txn.facts.set(
            "session.last_eot_ts",
            now_us,
            FactStatus.COMMITTED,
            Provenance(source="system", event_id=event_id, turn_id=turn.turn_id, step_no=step_no, ts_us=now_us),
            rule="turns.eot_settle_marker",
        )

        # V2: a pure backchannel ("mm-hmm") during ongoing work needs no
        # interpretation at all (I-11). A brand new utterance (no active
        # goal, or goal already finished) still goes through normal
        # interpretation, since "ok" might be starting something.
        full_text = " ".join(chunk.text for chunk in turn.chunks)
        gid = active_goal_id(txn.store)
        if gid is not None:
            goal = txn.store.goals.get(gid)
            if (
                goal is not None
                and goal.status == GoalStatus.ACTIVE
                and goal.task_state in (TaskState.PLANNING, TaskState.EXECUTING, TaskState.RESPONDING)
                and self._quick_detector.detect(full_text).is_backchannel
            ):
                return

        txn.facts.set(
            "session.pending_interpretation_turn",
            turn.turn_id,
            FactStatus.COMMITTED,
            Provenance(source="user", event_id=event_id, turn_id=turn.turn_id, step_no=step_no, ts_us=now_us),
            rule="turns.eot",
        )

    def on_interruption(self, ts_us: int, txn: StoreTxn, *, active_goal_id: str | None) -> Turn:
        """Open turn if none (§5.1 class 1); an already-open turn simply
        becomes the interruption turn — no new one is created."""
        turn_id = txn.turn_log.open_turn_id()
        if turn_id is not None:
            return txn.turn_log.get(turn_id)

        turn_id = txn.store.ids.next("turn")
        turn = txn.turn_log.open_turn(turn_id, ts_us, is_interruption=True, during_goal=active_goal_id)
        txn.set_floor(FloorState.USER_TURN_OPEN)
        return turn
