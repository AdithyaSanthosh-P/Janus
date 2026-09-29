"""Honest replies (Config.conversational_replies_enabled).

Found in the 28 Sep live voice demo: with no active goal, every turn that
wasn't a new request got the same generic "didn't catch that" re-ask --
"thank you", "has it been booked?", and, worst, "book me a hotel" (no hotel
tool) was forced onto the nearest tool and became a flight search. These
builders produce the honest text instead; `kernel/responder.py.FastResponder.
_honest_reply` speaks it once per turn. Every status claim is built from
what actually ran (the call ledger), never from model text.
"""

from __future__ import annotations

from prism_rt.model.types import CallStatus, FactStatus, GoalStatus, Provenance

HONEST_REPLY_KEY = "session.honest_reply"


def _human(name: str) -> str:
    return name.replace("_", " ")


def capabilities_text(store, limit: int = 5) -> str:
    names = [_human(t.name) for t in store.catalog.usable_tools()][:limit]
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    return f" I can help with things like {listed}."


def unsupported_text(store, what: str) -> str:
    # Quoted, not spliced into a sentence: the model's phrase is often a noun
    # phrase ("team maker tool"), which read as "I can't team maker tool".
    return f"Sorry — \"{what.rstrip('.')}\" isn't something I can do here.{capabilities_text(store)}"


def done_summary(store, goal_id: str) -> str:
    """What actually ran for this goal, from the call ledger."""
    parts = []
    for call in store.call_ledger.all():
        if call.goal_id == goal_id and call.status == CallStatus.CONSUMED:
            values = ", ".join(str(v) for v in call.args.values())
            parts.append(f"{_human(call.tool)} ({values})" if values else _human(call.tool))
    return "I ran " + "; ".join(parts) + "." if parts else ""


def last_finished_goal(store):
    """The goal whose FINAL went out most recently, or None."""
    fact = store.facts.get("session.last_finished_goal")
    if fact is None or fact.status == FactStatus.RETRACTED or not fact.value:
        return None
    return store.goals.get(fact.value)


def status_text(store) -> str:
    """Answer "did that go through?" about the most recent finished goal."""
    last = last_finished_goal(store)
    if last is None:
        return "I haven't done anything yet — what would you like me to do?"
    done = done_summary(store, last.goal_id)
    if last.status == GoalStatus.ABANDONED:
        tail = " It didn't finish, so nothing else was done." if done else " It didn't finish, so nothing was done."
    else:
        tail = " Nothing else was done."
    return (f"For your last request, {done}" if done else "For your last request, no action completed.") + tail


def salvage_text(store, goal_id: str) -> str:
    done = done_summary(store, goal_id)
    return "I couldn't finish this in time." + (f" So far, {done}" if done else " Nothing has been done yet.")


POLITE_REPLY = "Happy to help — what would you like me to do?"


def set_honest_reply(txn, turn_id: str, text: str, now_us: int, step_no: int, *, event_id: str) -> None:
    txn.facts.set(
        HONEST_REPLY_KEY,
        {"turn": turn_id, "text": text},
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=event_id, turn_id=turn_id, step_no=step_no, ts_us=now_us),
        rule="replies.honest_reply",
    )
