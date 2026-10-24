#!/usr/bin/env python3
"""Pass/fail table for a batch of scripted demo scenes (scripts/demo_driver.py).

Reads the batch log written by batch.sh / batch_reset.sh (sections "=== <scene> <time>")
and checks each scene against what it must say, must not say, and a generic rule:
every spoken turn gets an answer from Janus (a "silent turn" is flagged).

Usage: scripts/demo_check.py <batch log> [--verbose]
"""

from __future__ import annotations

import re
import sys

# scene -> (must match, must not match, silent turns allowed). Patterns are regexes over the scene's log text.
EXPECT: dict[str, tuple[list[str], list[str], int]] = {
    "correction": ([r"call_cancelled\s+c-0001: You changed it to 21", r"The thermostat is now 21"], [r"CONFIRMED: Janus Thermostat 22"], 0),
    "lock": ([r"already locked|now locked"], [], 0),
    "unlock_yes": ([r"security action"], [r"CONFIRMED: Janus Lock unlocked"], 0),
    "unconfirmed": ([r"couldn't confirm|not confirmed"], [r"is now off"], 0),
    "unreachable": ([r"couldn't reach SmartThings"], [], 0),
    "modeldown": ([r"couldn't process that"], [], 0),
    "slowmodel": ([r"switch"], [], 0),
    "offer_withdraw": ([r"Never mind: the garage is already closed"], [], 0),
    "offer_yes": ([r"garage is now closed"], [], 0),
    "offer_no": ([r"dropped that request|Nothing was done"], [], 0),
    "camera": ([r"4C|Never mind: the washer"], [], 0),
    "camera_explain": ([r"4C", r"water is not being supplied"], [], 0),
    "status": ([r"front door"], [], 0),
    "garage_self_interrupt": ([r"Never mind: the garage is already closed"], [], 0),
    "barge_in": ([r"Friday afternoon"], [], 0),
    "recap": ([r"Nothing yet|Done:"], [], 0),
    "recap3": ([r"Nothing yet|Done:|Never sent"], [], 0),
    "washer_offer_yes": ([r"booked|confirmed"], [], 0),
    "repeat": ([r"already 23"], [], 0),
    "smalltalk": ([r"Good morning", r"You're welcome"], [], 0),
    "washer_offer_blank": ([r"shows no error"], [], 0),
    "washer_wall": ([r"can't make out the washer's display"], [], 0),
    "washer_clears_then_ask": ([r"Never mind: the washer's error code is gone", r"no error"], [], 0),
    "washer_offer_clears": ([r"Never mind: the washer's error code is gone"], [], 0),
    "photo_clean": ([r"Never mind: the washer's error code is gone"], [], 0),
    "photo_phone": ([r"Never mind: the washer's error code is gone"], [], 0),
    "washer_offer_fix": ([r"Friday afternoon"], [], 0),
    "alarm": ([r"alarm is set for 6:30 AM"], [], 0),
    "alarm_correction": ([r"7 AM"], [], 0),
    "alarm_repeat": ([r"Shall I set an alarm for 6:30 AM again"], [], 0),
    "alarm_hedge": ([r"Just to check", r"alarm is set for 6:30 AM"], [], 0),
    "alarm_hedge_no": ([r"7 AM"], [r"not a time"], 0),
    "timer": ([r"timer for 5 minutes"], [], 0),
    "stress_rapid_changes": ([r"thermostat is now 24"], [r"CONFIRMED: Janus Thermostat 2[02]"], 0),
    "stress_cancel_all": ([r"dropped that request|Nothing was done"], [r"CONFIRMED: Janus Switch"], 0),
    "stress_offer_confusion": ([], [], 99),
    "voice_fast": ([], [], 99),
    "voice_quiet": ([r"front door"], [], 1),
    "voice_noisy": ([r"garage"], [], 0),
    "voice_speakers": ([r"front door"], [], 0),
    "voice_background_talker": ([r"thermostat is now 21"], [], 0),
    "voice_hesitant": ([r"thermostat is now 20"], [], 0),
    "voice_pause_mid_request": ([r"thermostat is now 22"], [], 0),
    "voice_monologue": ([r"thermostat is now 23"], [], 0),
    "voice_garbled": ([r"didn't quite catch|say that again"], [], 0),
    "voice_rapid_fire": ([r"timer"], [], 2),  # three requests merged into one plan
    "voice_wake_word_gap": ([r"timer for 3 minutes"], [], 0),
    "twist_injection": ([r"security action"], [r"is now unlocked"], 0),
    "twist_compound": ([r"committed=3"], [], 0),
    "twist_contradiction": ([r"security action"], [], 0),
    "twist_unknown_things": ([r"can't help|isn't something I can do|couldn't unlock|can't find"], [], 0),
    "twist_talk_over_janus": ([r"Saturday morning"], [], 0),
    "twist_back_door_yes": ([r"can't find a back door"], [r"CONFIRMED: Janus Lock"], 0),
    "twist_chat_while_asked": ([r"Yes, I'm here", r"still waiting for your answer"], [], 0),
    "greet_early": ([r"Hi!"], [], 0),
    "washer_status_no_camera": ([r"4C|error"], [], 0),
    "washer_status_wall": ([r"can't make out"], [r"34 minutes"], 0),
    "booking_bad_date": ([r"not a day I can book|couldn't book"], [r"booked for Nope|appointment.*Nope"], 0),
    "booking_no_diagnosis": ([r"couldn't book|Nothing has been diagnosed|no diagnosis"], [r"has been booked|is booked"], 0),
    "intro_questions": ([r"Yes, I'm here", r"what I can|I can control"], [], 0),
    "mixed_session_a": ([], [], 99),
    "mixed_session_b": ([], [], 99),
}


def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    name = None
    for line in text.splitlines():
        m = re.match(r"=== (\S+) \d\d:\d\d:\d\d", line)
        if m and m.group(1) != "DONE":
            name = m.group(1)
            out.setdefault(name, [])
            continue
        if line.startswith("=== DONE"):
            name = None
            out.pop("DONE", None)
        elif name:
            out[name].append(line)
    return out


def silent_turns(lines: list[str]) -> int:
    """Spoken turns that got no answer before the next turn (or the end of the scene)."""
    silent, pending = 0, False
    for line in lines:
        if "YOU SAY" in line:
            silent += pending
            pending = True
        elif re.search(r"agent_said\s+\[(?!filler)", line):
            pending = False
    return silent + pending


def main() -> int:
    path = sys.argv[1]
    verbose = "--verbose" in sys.argv
    raw = open(path).read()
    secs = sections(raw)
    finished = "=== DONE" in raw
    rows, bad = [], 0
    for i, (name, lines) in enumerate(secs.items()):
        if not finished and i == len(secs) - 1:
            rows.append((name, "RUN", "still running"))
            continue
        text = "\n".join(lines)
        rule = EXPECT.get(name)
        problems: list[str] = []
        if "Traceback" in text or "bad event" in text:
            problems.append("exception in the run")
        if "agent_said" not in text:
            problems.append("Janus never spoke")
        if rule is None:
            rows.append((name, "INFO", "no expectations written"))
            continue
        must, must_not, silent_ok = rule
        for pattern in must:
            if not re.search(pattern, text):
                problems.append(f"missing /{pattern}/")
        for pattern in must_not:
            if re.search(pattern, text):
                problems.append(f"found /{pattern}/")
        silent = silent_turns(lines)
        if silent > silent_ok and silent_ok != 99:
            problems.append(f"{silent} turn(s) got no answer")
        if problems:
            bad += 1
            rows.append((name, "FAIL", "; ".join(problems)))
        else:
            rows.append((name, "INFO" if silent_ok == 99 else "PASS", f"{silent} silent turn(s)" if silent else ""))
    width = max((len(r[0]) for r in rows), default=10)
    for name, verdict, why in rows:
        print(f"{name:<{width}}  {verdict:<4}  {why}")
        if verbose and verdict == "FAIL":
            print("    " + "\n    ".join(l for l in secs[name] if "agent_said" in l or "YOU SAY" in l)[:1200])
    total = len(rows)
    print(f"\n{sum(r[1] == 'PASS' for r in rows)} pass, {bad} fail, {sum(r[1] == 'INFO' for r in rows)} info (judged by hand), of {total} scenes")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
