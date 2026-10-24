"""Phone actions (demo mode): an alarm or a timer set on the user's own phone.

The companion app runs them. A tool call is sent to the app as JSON on the data
topic `janus.phone` ({"type": "phone_action", "id", "action", ...}); the app
hands it to the phone's clock app (Android's AlarmClock intents, with no
screen of their own) and answers on the same topic ({"type": "phone_result",
"id", "ok", "error"?}). The reply is the tool result:

- ok -> done; the answer is said word for word (`say_result`, as the recap);
- refused (no clock app, a bad value) -> the call fails, and the kernel says
  so instead of claiming the alarm;
- no answer in time -> `outcome: "unconfirmed"`, the kernel's "sent, not
  confirmed": the phone may have set it.

A request while no phone is connected fails at once. `PhoneToolset.publish` is
set by the host once the room is connected (as RecapToolset.store_getter);
`on_reply` takes the app's answers.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from typing import Awaitable, Callable

TOPIC = "janus.phone"
REPLY_TIMEOUT_S = 8.0

MANIFEST: list[dict] = [
    {
        "name": "phone_set_alarm",
        "description": "Set an alarm on the user's phone. The time is on a 24-hour clock as HH:MM: 6:30 in the morning is "
        "06:30, 7 in the evening is 19:00. When the user does not say morning or evening, choose the next time that "
        "time comes round. Janus cannot see or check the phone's existing alarms: a question about whether an "
        "alarm is set is not a request to set one.",
        "mutability": "state_changing",
        # Each alarm is its own; the same one asked for again is asked about,
        # since the user may have deleted it (Janus cannot see the phone's alarms).
        "repeatable": True,
        "ask_before_repeat": True,
        "say_result": True,
        "label": "Set an alarm for {time}",
        "params_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "The alarm time, 24-hour HH:MM, for example 06:30 or 19:00."},
                "label": {"type": "string", "description": "What the alarm is for, if the user said it, for example gym."},
            },
            "required": ["time"],
        },
    },
    {
        "name": "phone_set_timer",
        "description": "Start a countdown timer on the user's phone.",
        "mutability": "state_changing",
        "repeatable": True,
        "say_result": True,
        "label": "Start a {minutes}-minute timer",
        "params_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "The timer's length in minutes, for example 5 or 0.5."},
                "label": {"type": "string", "description": "What the timer is for, if the user said it, for example pasta."},
            },
            "required": ["minutes"],
        },
    },
]


class PhoneUnavailable(RuntimeError):
    pass


class PhoneRefused(RuntimeError):
    pass


_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


def _number_words(words: list[str]) -> int | None:
    """"seven" -> 7, "forty five" -> 45; None when the words are not a number up to 59."""
    if len(words) == 1 and words[0] in _UNITS:
        return _UNITS[words[0]]
    if len(words) == 1 and words[0] in _TENS:
        return _TENS[words[0]]
    if len(words) == 2 and words[0] in _TENS and 1 <= _UNITS.get(words[1], 0) <= 9:
        return _TENS[words[0]] + _UNITS[words[1]]
    return None


def parse_time(value) -> tuple[int, int]:
    """(hour, minute) from "06:30", "6:30", "0630", "7", "seven", "seven thirty",
    "7 pm" or "seven a.m."; ValueError otherwise."""
    text = str(value or "").strip().lower().replace(".", ":")
    meridiem = None
    found = re.fullmatch(r"(.*?)\s*([ap])\s*:?\s*m\s*:?", text)
    if found is not None:
        text, meridiem = found.group(1).strip(), found.group(2)
    text = re.sub(r"\s*o'?clock$", "", text).strip()
    match = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?", text)
    if match is not None:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
    else:
        words = [w for w in re.split(r"[\s-]+", text) if w]
        # "seven", "seven thirty", "six forty five": the longest hour first, so
        # "twenty one" is 21:00, not 20:01.
        hour = minute = None
        for cut in range(len(words), 0, -1):
            h, m = _number_words(words[:cut]), _number_words(words[cut:]) if cut < len(words) else 0
            if h is not None and m is not None:
                hour, minute = h, m
                break
        if hour is None:
            raise ValueError(f"not a time: {value!r}")
    if meridiem == "p" and hour < 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"not a time: {value!r}")
    return hour, minute


def spoken_time(hour: int, minute: int) -> str:
    """"6:30 AM", "7 PM", "12:15 PM": how the alarm is said back."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12} {suffix}" if minute == 0 else f"{h12}:{minute:02d} {suffix}"


def spoken_minutes(minutes: float) -> str:
    seconds = round(minutes * 60)
    whole, rest = divmod(seconds, 60)
    parts = [f"{whole} minute" + ("" if whole == 1 else "s")] if whole else []
    if rest or not whole:
        parts.append(f"{rest} second" + ("" if rest == 1 else "s"))
    return " ".join(parts)


class PhoneToolset:
    def __init__(self, *, timeout_s: float = REPLY_TIMEOUT_S) -> None:
        self.manifest = MANIFEST
        self.publish: Callable[[dict], Awaitable[None]] | None = None
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)

    def on_reply(self, payload: dict) -> None:
        if not isinstance(payload, dict) or payload.get("type") != "phone_result":
            return
        future = self._pending.get(str(payload.get("id")))
        if future is not None and not future.done():
            future.set_result(payload)

    def make_executor(self) -> Callable[[str, dict], Awaitable[dict]]:
        async def execute(tool_name: str, args: dict) -> dict:
            if tool_name == "phone_set_alarm":
                hour, minute = parse_time(args.get("time"))
                request = {"action": "set_alarm", "hour": hour, "minute": minute}
                said = f"Your alarm is set for {spoken_time(hour, minute)}."
            elif tool_name == "phone_set_timer":
                minutes = float(args.get("minutes") or 0)
                if not 0 < minutes <= 24 * 60:
                    raise ValueError(f"not a timer length: {args.get('minutes')!r}")
                request = {"action": "set_timer", "seconds": round(minutes * 60)}
                said = f"Your timer for {spoken_minutes(minutes)} has started."
            else:
                raise ValueError(f"unknown tool {tool_name!r}")
            if args.get("label"):
                request["label"] = str(args["label"])[:40]
            return await self._run(request, said)

        return execute

    async def _run(self, request: dict, said: str) -> dict:
        if self.publish is None:
            raise PhoneUnavailable("no phone is connected")
        rid = f"p-{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            await self.publish({"type": "phone_action", "id": rid, **request})
            try:
                reply = await asyncio.wait_for(future, self._timeout_s)
            except asyncio.TimeoutError:
                return {"outcome": "unconfirmed", "sent": True, "wanted": request}
        finally:
            self._pending.pop(rid, None)
        if not reply.get("ok"):
            raise PhoneRefused(f"the phone refused: {reply.get('error') or 'no reason given'}")
        return {"done": True, "say": said, **{k: v for k, v in request.items() if k != "action"}}
