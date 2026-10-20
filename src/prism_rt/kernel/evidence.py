"""Evidence-bound confirmation (`Config.evidence_bound_confirmation_enabled`).

A write held for the user's yes (CommitGate G4/G12, asked by
`responder._confirm_unconfirmed_write`) was asked about a particular world:
what the camera showed for the request, and the state of the device the
write targets. The yes answers *that* question. If the world changes while
Janus waits, the old yes must not release the write:

- the device reaches the requested state by itself ("someone else unlocked
  it"): the write is withdrawn, unsent, and the user is told
  (`reducers._apply_world_fact`);
- anything else the question rested on changes (the error code on the
  display, the device's state): CommitGate G13 holds the write, and the
  responder says what changed and asks again.

Evidence keys:
- `slot.<goal>.<name>` facts written by perception (goal-level, not a
  compiled action's own `slot.<goal>.a<i>.<name>` key);
- `world.<target_key(arg)>`, for a tool that declares `watch` (the argument
  naming the device). The host reports the device's state as a `world_fact`
  event under the same key (`prism_rt.canonical.world_target_key`).

The snapshot is taken when the question is asked, as a sorted list of
[key, value] pairs (`confirm.<call>.evidence`), values as observed and
compared in `same_form`. A key with no value yet
(the host's first report of the device arrives just after the question)
takes the first value reported as its baseline, never as a change.
"""

from __future__ import annotations

import re

from prism_rt.canonical import world_target_key
from prism_rt.model.types import CallStatus, FactStatus

SNAPSHOT = "confirm.{call_id}.evidence"


def _active(fact) -> bool:
    return fact is not None and fact.status != FactStatus.RETRACTED


def world_key(store, call) -> str | None:
    tool = store.catalog.get(call.tool)
    if tool is None or not tool.watch:
        return None
    arg = call.args.get(tool.watch)
    if not isinstance(arg, str) or not arg.strip():
        return None
    return "world." + world_target_key(arg)


def done_value(store, call) -> str | None:
    """The device value at which the write has nothing left to do, from the
    tool's `done_when` template ("locked", "{state}", "{celsius}")."""
    tool = store.catalog.get(call.tool)
    if tool is None or not tool.done_when:
        return None
    try:
        return same_form(tool.done_when.format(**{k: v for k, v in call.args.items()}))
    except (KeyError, IndexError, ValueError):
        return None


def same_form(value) -> str | None:
    """Values compared as text: 22, 22.0 and "22" are one value."""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    text = str(value).strip().lower()
    return text[:-2] if re.fullmatch(r"-?\d+\.0", text) else text


def _perception_keys(store, goal_id: str) -> list[str]:
    action_key = re.compile(rf"slot\.{re.escape(goal_id)}\.a\d+\.")
    return sorted(
        key for key, fact in store.facts.by_prefix(f"slot.{goal_id}.").items()
        if _active(fact) and fact.provenance.source == "perception" and not action_key.match(key)
    )


def snapshot(store, call) -> list[list]:
    keys = _perception_keys(store, call.goal_id)
    wk = world_key(store, call)
    if wk is not None:
        keys.append(wk)
    pairs = []
    for key in keys:
        fact = store.facts.get(key)
        pairs.append([key, fact.value if _active(fact) else None])
    return pairs


def recorded(store, call_id: str) -> list[list] | None:
    fact = store.facts.get(SNAPSHOT.format(call_id=call_id))
    return fact.value if _active(fact) and isinstance(fact.value, list) else None


def changes(store, call) -> list[tuple[str, str, str]]:
    """(key, value when asked, value now) for every evidence key that now
    holds a different value. A key with no value now (a camera reading
    between lease renewals) or no value when asked (baseline not yet
    reported) is not a change."""
    pairs = recorded(store, call.call_id)
    if not pairs:
        return []
    out = []
    for key, old in pairs:
        fact = store.facts.get(key)
        if old is None or not _active(fact):
            continue
        if same_form(fact.value) != same_form(old):
            out.append((key, old, fact.value))
    return out


def held_for_yes(store, call) -> bool:
    asked = store.facts.get(f"confirm.{call.call_id}.asked")
    return call.status == CallStatus.PROPOSED and _active(asked) and recorded(store, call.call_id) is not None


def describe(store, call, key: str) -> str:
    """Words for an evidence key: the device as the user named it, or the
    camera reading's name ("code", "led color")."""
    if key.startswith("world."):
        tool = store.catalog.get(call.tool)
        return str(call.args.get(tool.watch, "the device")) if tool is not None and tool.watch else "the device"
    return key.rsplit(".", 1)[-1].replace("_", " ")
