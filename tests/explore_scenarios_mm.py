"""Multimodal race scenarios for `sim/explorer.py` (M4 / C17): vision
(V1-V4) and ASR (A1-A4).

Each scenario scripts per-observation results -- vision prompts carry
`frame: <obs_id>`, ASR prompts `clip: <obs_id>` -- so evidence from the
*wrong* observation produces a detectably wrong value. That is what the
scenario checks look for; the explorer's 11 generic oracles (full
TraceChecker, floor rule, TRIAGE hold, FINAL snapshot + text grounding, invalidated-never-
cancelled, one confirmed write per lineage, liveness incl. a floor stuck
open, W4, per-scenario semantics) run on every schedule as well.

Invariants each scenario states (checked below, on top of the generic
oracles):
- stale evidence: once the user has redirected/corrected, no successful
  FINAL may be grounded in the superseded observation's value;
- lost intent: if the goal wasn't already answered before the redirect,
  the redirected request is answered (exactly once);
- lineage/order: ASR transcripts enter the conversation in the order the
  audio was captured, never in the order transcription happened to finish;
- failure honesty: a failed perception/ASR job is never treated as
  evidence, and never leaves the agent silent.

Observation ids are global across modalities, in arrival order: the
first frame/clip is o-0001, the next o-0002, and so on (event order is
preserved by every schedule the explorer generates).
"""

from __future__ import annotations

from conftest import SEARCH_FLIGHTS_TOOL

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.explorer import Scenario, TimedEvent
from prism_rt.workers.gateway import ScriptedProvider

LOOKUP_COLOR_TOOL = {
    "name": "lookup_color",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"]},
}

VISION_WORKERS = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.VISION: 50_000}
ASR_WORKERS = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.ASR: 50_000}


def _manifest(tools):
    return {"type": "manifest", "payload": {"tools": tools}}


def _chunk(text):
    return {"type": "text_chunk", "payload": {"text": text}}


def _eot():
    return {"type": "end_of_turn", "payload": {}}


def _interrupt():
    return {"type": "interruption", "payload": {}}


def _frame(fid):
    return {"type": "video_frame", "payload": {"frame_id": fid}}


def _clip(cid):
    return {"type": "audio_clip", "payload": {"clip_id": cid}}


def _watchdog():
    return {"type": "watchdog", "payload": {"wall_elapsed_s": 105.0}}


def _flaky(failures: int, response: dict):
    """A scripted response that errors on its first `failures` calls --
    a transient provider failure, exactly as a live 5xx surfaces."""
    state = {"calls": 0}

    def respond(prompt):
        state["calls"] += 1
        if state["calls"] <= failures:
            raise RuntimeError("scripted transient provider failure")
        return dict(response)

    return respond


def _always_fails(prompt):
    raise RuntimeError("scripted persistent provider failure")


def _finals(h):
    return [er.action for r in h.run_log.reports for er in r.emit_report.emitted if er.action.action_type == ActionType.FINAL]


def _successful_finals(h):
    return [a for a in _finals(h) if a.body.task_completed]


def _redirect_checks(slot: str, stale: str, fresh: str, redirect_index: int):
    """Stale evidence + lost intent + no duplicate answer, relative to the
    user's redirect/correction event at `redirect_index`."""

    def check(h, run):
        redirect_ts = run.schedule.event_ts[redirect_index]
        out = []
        answered_before = [a for a in _successful_finals(h) if a.ts_us < redirect_ts]
        after = [a for a in _finals(h) if a.ts_us >= redirect_ts]
        for a in after:
            if a.body.task_completed and a.snapshot and a.snapshot.slots.get(slot) == stale:
                out.append(f"stale-evidence: FINAL at {a.ts_us} grounded in {slot}={stale!r} after the redirect at {redirect_ts}")
        if not answered_before:
            fresh_finals = [a for a in after if a.body.task_completed and a.snapshot and a.snapshot.slots.get(slot) == fresh]
            if len(fresh_finals) != 1:
                out.append(f"lost-intent: expected exactly one answer with {slot}={fresh!r} after the redirect, got {len(fresh_finals)}")
        return out

    return check


# --- V1: vision result vs. a user redirect (AT_UTTERANCE) ----------------------


def _color_provider(*, first_frame=None, second_frame=None, mode="at_utterance"):
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'what color is this'", {"act": "new_goal", "intent": "color_check", "visual_reference": mode, "visual_candidates": [{"name": "color", "description": "the object's color"}]})
    p.register("interpret", "transcript: 'actually the other one'", {"act": "slot_update", "slot_deltas": [], "visual_reference": mode, "visual_candidates": [{"name": "color", "description": "the object's color"}]})
    p.register("plan", "goal_intent: color_check", {"steps": [{"local_id": "s1", "tool": "lookup_color", "kind": "read", "bindings": {"color": {"type": "fact", "key": "slot.$G.color"}}, "after": []}]})
    p.register("compose", "s1", {"text": "Here is what that color means.", "claims": ["result:s1"]})
    p.register("vision", "frame: o-0001", first_frame or {"claims": [{"name": "color", "value": "red", "confidence": "high"}]})
    p.register("vision", "frame: o-0002", second_frame or {"claims": [{"name": "color", "value": "blue", "confidence": "high"}]})
    return p


_VISION_CONFIG = Config(vision_enabled=True)

_REDIRECT_EVENTS = (
    TimedEvent(0, _manifest([LOOKUP_COLOR_TOOL])),
    TimedEvent(50_000, _frame("f1")),
    TimedEvent(100_000, _chunk("what color is this")),
    TimedEvent(150_000, _eot()),
    TimedEvent(300_000, _frame("f2")),
    TimedEvent(400_000, _interrupt()),
    TimedEvent(410_000, _chunk("actually the other one")),
    TimedEvent(420_000, _eot()),
)

V1_VISION_REDIRECT = Scenario(
    name="V1_vision_redirect",
    config=_VISION_CONFIG,
    tools={"lookup_color": {"latency_ms": 200, "response": {"meaning": "ok"}}},
    provider=_color_provider,
    events=_REDIRECT_EVENTS,
    worker_latency_us=VISION_WORKERS,
    check=_redirect_checks("color", "red", "blue", 5),
)


# --- V2: new frame vs. an old vision result (CURRENT_STATE) --------------------

V2_NEW_FRAME_VS_OLD_RESULT = Scenario(
    name="V2_new_frame_vs_old_result",
    config=_VISION_CONFIG,
    tools={"lookup_color": {"latency_ms": 200, "response": {"meaning": "ok"}}},
    provider=lambda: _color_provider(mode="current_state"),
    events=_REDIRECT_EVENTS,
    worker_latency_us=VISION_WORKERS,
    check=_redirect_checks("color", "red", "blue", 5),
)


# --- V3: vision failure vs. a redirect ----------------------------------------------


def _v3_checks(h, run):
    out = _redirect_checks("color", "red", "blue", 5)(h, run)
    # The failed frame must never have produced evidence.
    for key, fact in h.store.facts.by_prefix("claim.").items():
        if fact.value == "red":
            out.append(f"failure-honesty: failed vision job produced claim {key}={fact.value!r}")
    # No clarification asked once the redirect is being handled (the new
    # frame is analyzable, so perception can still answer).
    redirect_eot = run.schedule.event_ts[7]
    clarifies = [er.action for r in h.run_log.reports for er in r.emit_report.emitted if er.action.action_type == ActionType.CLARIFY and er.action.ts_us > redirect_eot]
    if clarifies:
        out.append(f"spurious-clarify: CLARIFY at {clarifies[0].ts_us} after the redirect, though the new frame could answer")
    return out


V3_VISION_FAILURE_VS_REDIRECT = Scenario(
    name="V3_vision_failure_vs_redirect",
    config=_VISION_CONFIG,
    tools={"lookup_color": {"latency_ms": 200, "response": {"meaning": "ok"}}},
    provider=lambda: _color_provider(first_frame=_always_fails),
    events=_REDIRECT_EVENTS,
    worker_latency_us=VISION_WORKERS,
    check=_v3_checks,
)


# --- V4: slow vision + watchdog + late correction -----------------------------


def _v4_checks(h, run):
    """§8.9's contract, not a stronger one: the watchdog forces one truthful
    FINAL if a goal is in progress; a genuine completion landing in the
    same step, or a goal first created after the watchdog, may still
    report success (Phase A's own same-step fix). What must never happen:
    no answer, two answers, or a success claim grounded in the frame the
    user redirected away from."""
    redirect_ts = run.schedule.event_ts[6]
    finals = _finals(h)
    out = []
    if len(finals) != 1:
        out.append(f"watchdog: expected exactly one FINAL, got {len(finals)}")
    for a in finals:
        if a.ts_us >= redirect_ts and a.body.task_completed and a.snapshot and a.snapshot.slots.get("color") == "red":
            out.append(f"stale-evidence: FINAL at {a.ts_us} claims success on the superseded frame after the redirect at {redirect_ts}")
    return out


V4_SLOW_VISION_WATCHDOG = Scenario(
    name="V4_slow_vision_watchdog",
    config=_VISION_CONFIG,
    tools={"lookup_color": {"latency_ms": 200, "response": {"meaning": "ok"}}},
    provider=_color_provider,
    events=(
        TimedEvent(0, _manifest([LOOKUP_COLOR_TOOL])),
        TimedEvent(50_000, _frame("f1")),
        TimedEvent(100_000, _chunk("what color is this")),
        TimedEvent(150_000, _eot()),
        TimedEvent(600_000, _watchdog()),
        TimedEvent(650_000, _frame("f2")),
        TimedEvent(700_000, _interrupt()),
        TimedEvent(710_000, _chunk("actually the other one")),
        TimedEvent(720_000, _eot()),
    ),
    worker_latency_us={**VISION_WORKERS, JobKind.VISION: 400_000},
    check=_v4_checks,
)


# --- ASR --------------------------------------------------------------------------


def _flight_provider(*, clip_a=None, clip_b=None):
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'Find flights to Pune'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "transcript: 'actually Mumbai'", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register("plan", "goal_intent: search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    p.register("compose", "s1", {"text": "Flights found.", "claims": ["result:s1"]})
    p.register("asr", "clip: o-0001", clip_a or {"segments": [{"text": "Find flights to Pune", "offset_us": 0, "end_us": 50_000}], "end_of_utterance": True})
    p.register("asr", "clip: o-0002", clip_b or {"segments": [{"text": "actually Mumbai", "offset_us": 0, "end_us": 50_000}], "end_of_utterance": True})
    return p


def _turn_texts(h):
    return [" ".join(c.text for c in t.chunks) for t in h.store.turn_log.all() if t.chunks]


def _asr_order_checks(redirect_index: int):
    def check(h, run):
        out = _redirect_checks("destination", "Pune", "Mumbai", redirect_index)(h, run)
        texts = _turn_texts(h)
        firsts = [i for i, t in enumerate(texts) if "Find flights to Pune" in t]
        seconds = [i for i, t in enumerate(texts) if "actually Mumbai" in t]
        if firsts and seconds and firsts[0] > seconds[0]:
            out.append(f"asr-order: earlier utterance entered the conversation after the later one (turns={texts})")
        for phrase in ("Find flights to Pune", "actually Mumbai"):
            n = sum(t.count(phrase) for t in texts)
            if n > 1:
                out.append(f"asr-duplicate: {phrase!r} entered the conversation {n} times (turns={texts})")
        return out

    return check


_AUDIO_ONLY = Config(asr_enabled=True, audio_mode="audio_only", asr_closes_turn=True)
_AUTO = Config(asr_enabled=True, audio_mode="auto")

# A1: an audio request, then a *text* correction, with a slow transcription
# of the audio racing the correction. In `auto` mode the harness closes the
# audio utterance with its own end_of_turn.
#
# AUTO mode's contract (`docs/prompt 2.txt` line 909): typed text arriving
# within `asr_dedupe_window_ms` (800) of a clip *is* that clip's transcript,
# so the ASR output is discarded. A spoken request followed by a typed
# correction inside that window is therefore indistinguishable from "the
# text is the transcript" -- the spec resolves it in favour of text, and
# the agent never hears a separate spoken request to correct. So: stale
# answers and out-of-order replay are always violations; the corrected
# answer is only *required* when the correction starts outside the window.


def _a1_checks(h, run):
    clip_ts, correction_ts = run.schedule.event_ts[1], run.schedule.event_ts[4]
    out = [v for v in _asr_order_checks(3)(h, run) if not v.startswith("lost-intent")]
    if correction_ts > clip_ts + h.config.asr_dedupe_window_ms * 1000:
        out.extend(v for v in _redirect_checks("destination", "Pune", "Mumbai", 3)(h, run) if v.startswith("lost-intent"))
    return out


A1_ASR_VS_TEXT_CORRECTION = Scenario(
    name="A1_asr_vs_text_correction",
    config=_AUTO,
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}}},
    provider=_flight_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _clip("a1")),
        TimedEvent(150_000, _eot()),
        TimedEvent(1_100_000, _interrupt()),
        TimedEvent(1_110_000, _chunk("actually Mumbai")),
        TimedEvent(1_120_000, _eot()),
    ),
    worker_latency_us=ASR_WORKERS,
    check=_a1_checks,
)

# A2: two spoken utterances, audio only; the earlier one's first
# transcription attempt fails transiently, so its retry can finish after
# the later utterance's transcript.
A2_EARLIER_VS_NEWER_SPEECH = Scenario(
    name="A2_earlier_vs_newer_speech",
    config=_AUDIO_ONLY,
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}}},
    provider=lambda: _flight_provider(clip_a=_flaky(1, {"segments": [{"text": "Find flights to Pune", "offset_us": 0, "end_us": 50_000}], "end_of_utterance": True})),
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _clip("a1")),
        TimedEvent(400_000, _clip("a2")),
    ),
    worker_latency_us=ASR_WORKERS,
    check=_asr_order_checks(2),
)

def _a3_checks(h, run):
    out = []
    if len(_successful_finals(h)) != 1:
        out.append(f"lost-intent: expected exactly one answer, got {len(_successful_finals(h))}")
    for t in _turn_texts(h):
        if t.count("Find flights to Pune") > 1:
            out.append(f"asr-duplicate: utterance entered the conversation twice in one turn ({t!r})")
    if sum(1 for t in _turn_texts(h) if "Find flights to Pune" in t) > 1:
        out.append(f"asr-duplicate: utterance entered the conversation in more than one turn ({_turn_texts(h)})")
    return out


# A3: text turn and an ASR transcript of the same utterance around EOT
# (auto mode's dedupe): the answer must come from the utterance once, and
# never be doubled or reordered.
A3_ASR_VS_EOT = Scenario(
    name="A3_asr_vs_eot",
    config=_AUTO,
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}}},
    provider=_flight_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _clip("a1")),
        TimedEvent(110_000, _chunk("Find flights to Pune")),
        TimedEvent(160_000, _eot()),
    ),
    worker_latency_us=ASR_WORKERS,
    check=lambda h, run: _a3_checks(h, run),
)

# A4: transcription fails persistently; the user then types the request.
# The failed clip must be recorded, never treated as content, and never
# block the typed request.


def _a4_checks(h, run):
    out = []
    if len(_successful_finals(h)) != 1:
        out.append(f"lost-intent: typed request not answered (finals={len(_successful_finals(h))})")
    failed = [k for k in h.store.facts.by_prefix("asr.") if k.endswith(".failed")]
    if len(failed) != 1:
        out.append(f"failure-honesty: exhausted ASR failure not recorded ({failed})")
    return out


A4_ASR_FAILURE_THEN_TEXT = Scenario(
    name="A4_asr_failure_then_text",
    config=_AUDIO_ONLY,
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}}},
    provider=lambda: _flight_provider(clip_a=_always_fails),
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _clip("a1")),
        TimedEvent(600_000, _chunk("Find flights to Pune")),
        TimedEvent(650_000, _eot()),
    ),
    worker_latency_us=ASR_WORKERS,
    check=_a4_checks,
)


# A4b: the transcription comes back *malformed* (a segment with no text);
# it must count as a failed attempt -- retried, then recorded -- never leave
# the clip "in progress" forever (which, with the TRIAGE hold waiting on
# untranscribed speech, would silence the agent).
A4B_MALFORMED_ASR_THEN_TEXT = Scenario(
    name="A4b_malformed_asr_then_text",
    config=_AUDIO_ONLY,
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}}},
    provider=lambda: _flight_provider(clip_a={"segments": [{"offset_us": 0}]}),
    events=A4_ASR_FAILURE_THEN_TEXT.events,
    worker_latency_us=ASR_WORKERS,
    check=_a4_checks,
)


VISION_SCENARIOS = (V1_VISION_REDIRECT, V2_NEW_FRAME_VS_OLD_RESULT, V3_VISION_FAILURE_VS_REDIRECT, V4_SLOW_VISION_WATCHDOG)
ASR_SCENARIOS = (A1_ASR_VS_TEXT_CORRECTION, A2_EARLIER_VS_NEWER_SPEECH, A3_ASR_VS_EOT, A4_ASR_FAILURE_THEN_TEXT, A4B_MALFORMED_ASR_THEN_TEXT)
MULTIMODAL_SCENARIOS = VISION_SCENARIOS + ASR_SCENARIOS
