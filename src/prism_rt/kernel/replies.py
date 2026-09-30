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


def last_goal_that_ran(store, exclude: str | None = None):
    """The most recent goal (other than `exclude`) with at least one call
    that actually ran, or None."""
    for goal in reversed(store.goals.all()):
        if goal.goal_id != exclude and done_summary(store, goal.goal_id):
            return goal
    return None


_SKIP_FIELDS = {"status", "message", "found", "error"}


def findings_text(store, limit: int = 4) -> str:
    """Short facts the most recent results reported ("device Aria R500 Wi-Fi
    router, booking id BK-0001"), newest first. Found live, 30 Sep: "what's
    my router's name?" right after a diagnosis that named it was refused as
    something the agent can't do. Only what a tool actually returned."""
    parts: list[str] = []
    for call in reversed(store.call_ledger.all()):
        if call.status != CallStatus.CONSUMED:
            continue
        fact = store.facts.get(f"result.{call.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if not isinstance(result, dict):
            continue
        for key, value in result.items():
            if key in _SKIP_FIELDS or not isinstance(value, str) or not value or len(value) > 60:
                continue
            item = f"{key.replace('_', ' ')} {value}"
            if item not in parts:
                parts.append(item)
            if len(parts) >= limit:
                return "What I found: " + "; ".join(parts) + "."
    return "What I found: " + "; ".join(parts) + "." if parts else ""


def status_text(store, *, with_findings: bool = False) -> str:
    """Answer "did that go through?" about the most recent finished goal;
    with_findings adds what the recent results said (a question about them)."""
    text = _status_text(store)
    findings = findings_text(store) if with_findings else ""
    return f"{text} {findings}" if findings else text


def _status_text(store) -> str:
    last = last_finished_goal(store)
    if last is None:
        return "I haven't done anything yet — what would you like me to do?"
    done = done_summary(store, last.goal_id)
    if not done and last.status == GoalStatus.ABANDONED:
        # Found live, 29 Sep: a request dropped before anything ran hid the
        # booking just before it ("was the flight booked?" -> "nothing was
        # done"). Say what the last request that did run achieved.
        earlier = last_goal_that_ran(store, exclude=last.goal_id)
        if earlier is not None:
            return (
                "Your last request was dropped before anything ran. Before that, "
                + done_summary(store, earlier.goal_id)
                + " That still stands."
            )
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


def abort_text(store, goal_id: str) -> str:
    """The FINAL for a cancelled request, saying what (if anything) already
    ran -- found live, 29 Sep: a bare "Okay, cancelled." after a booking
    had gone through read as if the booking itself was undone."""
    done = done_summary(store, goal_id)
    if done:
        return f"Okay, I've stopped. {done} That part already went through."
    earlier = last_goal_that_ran(store, exclude=goal_id)
    if earlier is not None:
        return "Okay, I've dropped that request. Nothing new was done — your earlier request still stands."
    return "Okay, I've dropped that request. Nothing was done."


def duplicate_write_text(call) -> str:
    """G5 blocked an identical write whose effect is confirmed: say which one."""
    values = ", ".join(str(v) for v in call.args.values())
    what = f"{_human(call.tool)} ({values})" if values else _human(call.tool)
    return f"I've already done that — {what} went through earlier, so I won't repeat it."


def confirmed_writes(store, goal_id: str) -> list:
    """The goal's state-changing calls that actually went through."""
    from prism_rt.model.types import StepKind

    return [
        call for call in store.call_ledger.all()
        if call.goal_id == goal_id and call.status == CallStatus.CONSUMED and call.kind == StepKind.WRITE
    ]


def no_second_write_text(calls) -> str:
    """"Make it evening" after a booking already went through -- found live,
    30 Sep: re-running the finished task made a second booking. Say what
    stands instead; there is no tool that changes a confirmed booking."""
    done = "; ".join(
        f"{_human(c.tool)} ({', '.join(str(v) for v in c.args.values())})" if c.args else _human(c.tool) for c in calls
    )
    return (
        f"That's already done — {done} went through. I can't change it from here, "
        "and I won't make a second one."
    )


def earlier_confirmed_writes(store, goal_id: str, tools) -> list:
    """Writes with one of `tools` that went through for an earlier goal."""
    from prism_rt.model.types import StepKind

    wanted = set(tools)
    return [
        call for call in store.call_ledger.all()
        if call.goal_id != goal_id and call.status == CallStatus.CONSUMED
        and call.kind == StepKind.WRITE and call.tool in wanted
    ]


def write_summary(calls) -> str:
    return "; ".join(
        f"{_human(c.tool)} ({', '.join(str(v) for v in c.args.values())})" if c.args else _human(c.tool) for c in calls
    )
