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

import re

from prism_rt.canonical import social_kind
from prism_rt.model.types import CallStatus, EffectStatus, FactStatus, GoalStatus, Provenance, StepKind

HONEST_REPLY_KEY = "session.honest_reply"


def _human(name: str) -> str:
    """A tool name as speech: "st_unlock" -> "unlock" (the pack prefix is not a word)."""
    return name.removeprefix("st_").replace("_", " ")


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


def unconfirmed_writes(store, goal_id: str) -> list:
    """The goal's writes that were sent but whose outcome is UNKNOWN
    (Config.unconfirmed_outcomes_enabled: tool-result status "unknown")."""
    if not store.config.unconfirmed_outcomes_enabled:
        return []
    out = []
    for call in store.call_ledger.all():
        if call.goal_id == goal_id and call.status == CallStatus.CONSUMED and call.kind == StepKind.WRITE:
            effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            if effect is not None and effect.status == EffectStatus.UNKNOWN:
                out.append(call)
    return out


NOTHING_PENDING_YES = "There's nothing waiting for a yes right now. What would you like me to do?"
NOTHING_PENDING_NO = "Okay. Nothing is waiting, so nothing was done."


def _clock_time(value: str) -> str:
    """"06:30" -> "6:30 AM", "19:00" -> "7 PM" (an alarm's 24-hour argument,
    said as a person would); any other value unchanged."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if match is None or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return value
    hour, minute = int(match.group(1)), int(match.group(2))
    h12 = hour % 12 or 12
    return f"{h12} {'AM' if hour < 12 else 'PM'}" if minute == 0 else f"{h12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


_TWENTY_FOUR_HOUR = re.compile(r"\b(0\d|1[3-9]|2[0-3]|00):([0-5]\d)\b(?!\s*[ap]\.?\s?m\b)", re.IGNORECASE)


def spoken_clock_times(text: str) -> str:
    """A model's own sentence with 24-hour times said as "6:30 AM"; a time
    already said with AM/PM, or "6:30" (no padding), is left alone."""
    return _TWENTY_FOUR_HOUR.sub(lambda m: _clock_time(m.group(0)), text)


def action_phrase(store, tool: str, args: dict) -> str | None:
    """"lock the front door": the tool's manifest `label` with its arguments
    filled in (a leading "the"/"my" in an argument is dropped, the label has
    its own), first letter lowered. None when the tool declares no label."""
    spec = store.catalog.get(tool) if store is not None else None
    if spec is None or not spec.label:
        return None
    values = {k: _clock_time(re.sub(r"^(the|my)\s+", "", v.strip(), flags=re.IGNORECASE)) if isinstance(v, str) else v
              for k, v in (args or {}).items()}
    try:
        text = spec.label.format(**values)
    except (KeyError, IndexError, ValueError):
        return None
    return text[:1].lower() + text[1:]


def stale_yes_text(store, call) -> str:
    """A yes that arrives after the offer it answers was withdrawn (the world
    changed while Janus waited, kernel/evidence.py): nothing is sent."""
    phrase = action_phrase(store, call.tool, call.args) or "do that"
    return (f"Nothing is waiting for a yes right now: I didn't {phrase}, because it changed while I was waiting. "
            "If you still want it, just ask.")


def unconfirmed_text(calls: list, store=None) -> str:
    phrases = [action_phrase(store, call.tool, call.args) for call in calls]
    if all(phrases):
        return ("I sent the command to " + "; ".join(phrases)
                + ", but I couldn't confirm it went through — please check the device before relying on it.")
    parts = []
    for call in calls:
        values = ", ".join(str(v) for v in call.args.values())
        parts.append(f"{_human(call.tool)} ({values})" if values else _human(call.tool))
    return "I sent " + "; ".join(parts) + ", but I couldn't confirm it went through — please check the device before relying on it."


_FAILURE_CLAIM = re.compile(
    r"\b(fail(?:ed|s|ure)?|could(?:n['\u2019]?t| not)|can(?:not|['\u2019]?t)|unable|did(?:n['\u2019]?t| not)|"
    r"was(?:n['\u2019]?t| not) able|not able|errors?|unsuccessful(?:ly)?|unfortunately|(?:did|was) not complete|not completed?)\b",
    re.IGNORECASE,
)


def claims_failure(text: str) -> bool:
    """Whether a sentence says something did not work. Deliberately broad:
    it only ever replaces a final whose writes all confirmed, with a line
    built from what ran, so a false positive costs wording, never truth."""
    return bool(_FAILURE_CLAIM.search(text or ""))


def confirmed_text(store, calls: list) -> str:
    """"Done: Janus Lock is now locked.": the writes that went through, from
    their own results when a result names the device and its new state,
    otherwise from the call itself."""
    parts = []
    for call in calls:
        fact = store.facts.get(f"result.{call.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if isinstance(result, dict) and result.get("device") and result.get("state") is not None:
            parts.append(f"{result['device']} is now {result['state']}")
        else:
            values = ", ".join(str(v) for v in call.args.values())
            parts.append(f"{_human(call.tool)} ({values})" if values else _human(call.tool))
    return "Done: " + "; ".join(parts) + "."


def device_write_final(store, goal_id: str) -> str | None:
    """Config.demo_speech_polish_enabled: the final for a request that was
    only device commands, every one confirmed or already in place, built from
    the results instead of the composer, which twice misread a confirmed
    result live (21 Oct: "failed to complete"; "reported it is already
    closed"). "The garage is now closed." / "The front door was already
    locked, so I didn't send anything." The device is named as the user named
    it. None (the composer's text stands) for anything else: reads, a step
    without a watched device, a write not confirmed."""
    plan = store.plans.current(goal_id)
    if plan is None or not plan.steps:
        return None
    parts, already_all, repeats_all = [], True, True
    for step in plan.steps:
        if step.kind != StepKind.WRITE:
            return None
        call = store.call_ledger.latest_by_step(goal_id, step.step_key)
        spec = store.catalog.get(step.tool)
        if call is None or call.status != CallStatus.CONSUMED or spec is None or not spec.watch:
            return None
        effect = store.effect_ledger.by_fingerprint(call.fingerprint)
        fact = store.facts.get(f"result.{call.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if effect is None or effect.status != EffectStatus.CONFIRMED or not isinstance(result, dict) or not result.get("done"):
            return None
        device = re.sub(r"^(the|my)\s+", "", str(call.args.get(spec.watch) or "").strip(), flags=re.IGNORECASE)
        state = result.get("state")
        if not device or state is None:
            return None
        state = f"{state} {spec.state_unit}" if spec.state_unit else str(state)
        already = bool(result.get("already"))
        already_all = already_all and already
        repeats_all = repeats_all and already and _repeats_earlier_write(store, call)
        parts.append(f"the {device} {'was already' if already else 'is now'} {state}")
    text = "; ".join(parts)
    text = text[:1].upper() + text[1:]
    if repeats_all:
        # Survey A5: the user asked again for something Janus already did.
        return f"{text}: I did that earlier, so I didn't send it again."
    return f"{text}, so I didn't send anything." if already_all else f"{text}."


def _norm_arg(value) -> str:
    text = " ".join(str(value).strip().lower().split())
    return re.sub(r"^(the|my)\s+", "", text)


def _repeats_earlier_write(store, call) -> bool:
    """Whether `call` (answered "already") asks for what an earlier request's
    write of the same tool and arguments did and confirmed in this session --
    a repeat of our own command, not a device someone else set."""
    want = {k: _norm_arg(v) for k, v in call.args.items()}
    for earlier in store.call_ledger.all():
        if earlier.goal_id == call.goal_id or earlier.tool != call.tool or earlier.status != CallStatus.CONSUMED:
            continue
        if {k: _norm_arg(v) for k, v in earlier.args.items()} != want:
            continue
        effect = store.effect_ledger.by_fingerprint(earlier.fingerprint)
        fact = store.facts.get(f"result.{earlier.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if effect is not None and effect.status == EffectStatus.CONFIRMED and isinstance(result, dict) \
                and result.get("done") and not result.get("already"):
            return True
    return False


def done_summary(store, goal_id: str) -> str:
    """What actually ran for this goal, from the call ledger."""
    unconfirmed = {c.call_id for c in unconfirmed_writes(store, goal_id)}
    parts = []
    for call in store.call_ledger.all():
        if call.goal_id == goal_id and call.status == CallStatus.CONSUMED and call.call_id not in unconfirmed:
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


_SKIP_FIELDS = {"status", "message", "found", "error", "reason"}


def findings_text(store, limit: int = 4) -> str:
    """Short facts the most recent results reported ("device Aria R500 Wi-Fi
    router, booking id BK-0001"), newest first. Found live, 30 Sep: "what's
    my router's name?" right after a diagnosis that named it was refused as
    something the agent can't do. Only what a tool actually returned."""
    parts: list[str] = []
    seen_steps: set[tuple] = set()
    for call in reversed(store.call_ledger.all()):
        if call.status != CallStatus.CONSUMED:
            continue
        step = (call.goal_id, call.step_key)
        if step in seen_steps:
            continue  # an older run of a step a correction re-ran: not a current finding
        seen_steps.add(step)
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
    if not done:
        # Found live, 29 Sep: a request dropped before anything ran hid the
        # booking just before it ("was the flight booked?" -> "nothing was
        # done"). Say what the last request that did run achieved. Same for a
        # request that needed nothing new because it had already gone
        # through (executor._finish_with_prior_effect).
        earlier = last_goal_that_ran(store, exclude=last.goal_id)
        if earlier is not None:
            lead = (
                "Your last request was dropped before anything ran."
                if last.status == GoalStatus.ABANDONED
                else "Your last request needed nothing new; it had already been done."
            )
            return f"{lead} Before that, {done_summary(store, earlier.goal_id)} That still stands."
    if last.status == GoalStatus.ABANDONED:
        tail = " It didn't finish, so nothing else was done." if done else " It didn't finish, so nothing was done."
    else:
        tail = " Nothing else was done."
    return (f"For your last request, {done}" if done else "For your last request, no action completed.") + tail


def salvage_text(store, goal_id: str) -> str:
    done = done_summary(store, goal_id)
    return "I couldn't finish this in time." + (f" So far, {done}" if done else " Nothing has been done yet.")


POLITE_REPLY = "Happy to help — what would you like me to do?"


def social_reply(text: str) -> str | None:
    """Demo speech polish: a reply that fits a greeting, thanks or goodbye
    (canonical.social_kind) -- "You're welcome." to "Thank you.", not an
    invitation to make a request. None for anything else."""
    kind = social_kind(text)
    if kind is None:
        return None
    lower = text.lower()
    if kind == "thanks":
        return "You're welcome. Bye for now." if re.search(r"\bbye\b|goodbye", lower) else "You're welcome."
    if kind == "bye":
        return "Bye for now."
    if kind == "how":
        return "I'm doing well, thanks. What can I do for you?"
    if kind == "greeting":
        part = re.search(r"good (morning|afternoon|evening)", lower)
        return f"Good {part.group(1)}! What can I do for you?" if part else "Hi! What can I do for you?"
    return "Anything else I can do?"


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


def duplicate_write_text(tool: str, args: dict, store=None) -> str:
    """An identical write whose effect is confirmed: say which one (with demo
    speech polish, as the action: "set an alarm for 6:30 AM")."""
    phrase = action_phrase(store, tool, args) if store is not None and store.config.demo_speech_polish_enabled else None
    if phrase:
        return f"That's already done: you asked me to {phrase} earlier and it went through, so I won't repeat it."
    values = ", ".join(str(v) for v in args.values())
    what = f"{_human(tool)} ({values})" if values else _human(tool)
    return f"I've already done that — {what} went through earlier, so I won't repeat it."


def _consumed_writes(store, keep) -> list:
    return [
        call for call in store.call_ledger.all()
        if call.status == CallStatus.CONSUMED and call.kind == StepKind.WRITE and keep(call)
    ]


def nothing_to_cancel_text(store) -> str:
    """"Cancel that" with nothing in progress -- found live, 30 Sep: the
    request came right after a booking went through and got no reply at
    all. Say what stands; no tool here undoes a confirmed write."""
    writes = _consumed_writes(store, lambda call: True)
    if writes:
        done = write_summary(writes[-1:])
        return (
            f"There's nothing in progress to cancel. {done[0].upper()}{done[1:]} already went through, "
            "and I can't cancel it from here."
        )
    return "There's nothing in progress to cancel. What would you like me to do?"


def confirmed_writes(store, goal_id: str) -> list:
    """The goal's state-changing calls that actually went through."""
    return _consumed_writes(store, lambda call: call.goal_id == goal_id)


def earlier_confirmed_writes(store, goal_id: str | None, tools) -> list:
    """Writes with one of `tools` that went through for any goal other than
    `goal_id` (None: any goal at all)."""
    wanted = {t for t in tools if not _repeatable(store, t)}
    return _consumed_writes(store, lambda call: call.goal_id != goal_id and call.tool in wanted)


def _repeatable(store, tool: str) -> bool:
    """A device command the manifest declares repeatable ("resume" after
    "stop" is a new command, not a second booking)."""
    spec = store.catalog.get(tool)
    return bool(spec is not None and spec.repeatable)


def write_summary(calls, store=None) -> str:
    """"book technician (Friday, afternoon)"; with demo speech polish (a
    `store` given) and manifest labels, the actions as said: "book a
    technician for Friday afternoon"."""
    if store is not None and store.config.demo_speech_polish_enabled:
        phrases = [action_phrase(store, c.tool, c.args) for c in calls]
        if phrases and all(phrases):
            return _join_and(phrases)
    return "; ".join(
        f"{_human(c.tool)} ({', '.join(str(v) for v in c.args.values())})" if c.args else _human(c.tool) for c in calls
    )


def _join_and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def no_second_write_text(calls, store=None) -> str:
    """"Make it evening" after a booking already went through -- found live,
    30 Sep: re-running the finished task made a second booking. Say what
    stands instead; there is no tool that changes a confirmed booking."""
    return (
        f"That's already done — {write_summary(calls, store)} went through. I can't change it from here, "
        "and I won't make a second one."
    )

_RECAP_TOOL = "session_recap"
_NEVER_SENT = {CallStatus.CANCELLED, CallStatus.INVALIDATED, CallStatus.STALE, CallStatus.DISCARDED}


def _recap_phrase(store, call) -> str:
    phrase = action_phrase(store, call.tool, call.args)
    if phrase:
        return phrase
    values = ", ".join(str(v) for v in call.args.values())
    return f"{_human(call.tool)} ({values})" if values else _human(call.tool)


def _spoken_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1]


def session_recap_text(store) -> str:
    """What this conversation did to the world, and what it did not do, from
    the call and effect ledgers (the `session_recap` demo tool; survey H4):
    "Done: set the thermostat to 21 degrees. Never sent: set the thermostat to
    22 degrees, and close the garage. Sent but not confirmed: turn the switch
    off." Each action is listed once, under its final outcome; an action
    withdrawn and then done after all counts as done."""
    groups: dict[str, list[str]] = {"done": [], "already": [], "unconfirmed": [], "failed": [], "never": []}
    for call in store.call_ledger.all():
        if call.kind != StepKind.WRITE or call.tool == _RECAP_TOOL:
            continue
        phrase = _recap_phrase(store, call)
        effect = store.effect_ledger.by_fingerprint(call.fingerprint) if call.emitted_ts_us is not None else None
        fact = store.facts.get(f"result.{call.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if call.emitted_ts_us is None:
            if call.status in _NEVER_SENT:
                groups["never"].append(phrase)
            continue
        if effect is None:
            continue
        if effect.status == EffectStatus.UNKNOWN:
            groups["unconfirmed"].append(phrase)
        elif effect.status == EffectStatus.FAILED or (isinstance(result, dict) and any(result.get(k) is False for k in ("done", "booked", "opened"))):
            groups["failed"].append(phrase)
        elif effect.status == EffectStatus.CONFIRMED:
            groups["already" if isinstance(result, dict) and result.get("already") else "done"].append(phrase)
    seen: set[str] = set()
    for key in ("done", "already", "unconfirmed", "failed", "never"):
        kept = []
        for phrase in groups[key]:
            if phrase.lower() not in seen:
                seen.add(phrase.lower())
                kept.append(phrase)
        groups[key] = kept
    parts = []
    for key, head in (("done", "Done"), ("already", "Already that way, so not sent again"),
                      ("unconfirmed", "Sent but not confirmed"), ("failed", "Failed"), ("never", "Never sent")):
        if groups[key]:
            parts.append(f"{head}: {_spoken_list(groups[key])}.")
    if not parts:
        return "Nothing yet: I haven't sent or held back any action in this conversation."
    return " ".join(parts)


def said_result_final(store, goal_id: str) -> str | None:
    """Config.demo_speech_polish_enabled: the final for a request that was only
    tools declared `say_result` (the recap) is their result's `say` text,
    word for word -- never the composer's paraphrase of it. None otherwise."""
    plan = store.plans.current(goal_id)
    if plan is None or not plan.steps:
        return None
    texts = []
    for step in plan.steps:
        spec = store.catalog.get(step.tool)
        call = store.call_ledger.latest_by_step(goal_id, step.step_key)
        if spec is None or not spec.say_result or call is None or call.status != CallStatus.CONSUMED:
            return None
        fact = store.facts.get(f"result.{call.call_id}")
        result = fact.value if fact is not None and fact.status != FactStatus.RETRACTED else None
        if not isinstance(result, dict) or not isinstance(result.get("say"), str) or not result["say"].strip():
            return None
        texts.append(result["say"].strip())
    return " ".join(texts)
