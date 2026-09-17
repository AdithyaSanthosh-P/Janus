"""TurnManager: turn assembly, chunk buffering, prefix digest.

The prefix digest is a real fact (`turn.<turn_id>.prefix`, Appendix A of
`docs/sonnet_implementation_plan.md`) — not just bookkeeping. An INTERPRET
job's read set includes it, so a chunk arriving while interpretation is in
flight invalidates that job automatically through the same read-set
mechanism as everything else, with no special-casing.
"""

from __future__ import annotations

from prism_rt.canonical import compute_digest
from prism_rt.model.types import FactStatus, FloorState, Provenance, Turn
from prism_rt.store.session import StoreTxn


class TurnManager:
    def on_chunk(self, text: str, event_id: str, ts_us: int, txn: StoreTxn, *, active_goal_id: str | None) -> Turn:
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
        return turn

    def on_eot(self, ts_us: int, txn: StoreTxn) -> Turn | None:
        turn_id = txn.turn_log.open_turn_id()
        if turn_id is None:
            return None
        turn = txn.turn_log.close_turn(turn_id, ts_us)
        txn.set_floor(FloorState.USER_TURN_CLOSED)
        return turn

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
