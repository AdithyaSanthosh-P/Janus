"""Named Config profiles for specific deployment targets.

`fdb_v3_config()` (Day 2, docs/fdb_v3_day2_plan.md WP1 /
docs/fdb_v3_implementation_plan.md §5): the FDB-v3 benchmark's own
policy needs, layered on top of `DEFAULT_CONFIG` as ordinary flags --
nothing here is a structural fork, matching the project's own rule that
every unknown/benchmark-specific behavior is a named policy setting,
never a code path split. Every flag it sets already exists and defaults
off/unchanged; this is just one place that turns on the combination FDB
needs, instead of every caller repeating the same five kwargs.
"""

from __future__ import annotations

import dataclasses

from prism_rt.config import DEFAULT_CONFIG, Config


# FDB-v3's lookup tools (its manifest declares no mutability at all). Tool
# metadata the deployment supplies -- the same thing a tool annotation would
# say -- not anything about the benchmark's scenarios.
FDB_READ_ONLY_TOOLS = (
    "search_flights",
    "get_card_benefits",
    "get_exchange_rate",
    "search_apartments",
    "calculate_commute",
    "track_order",
    "search_products",
)


def fdb_v3_config(**overrides) -> Config:
    """The FDB-v3 benchmark profile:

    - g4_exempt_undeclared_mutability: FDB declares no tool mutability,
      so every one of its 12 tools defaults to STATE_CHANGING (the
      catalog's safe default) -- an explicit Interpreter commit_intent
      would then gate even a plain read like search_flights. Exempting
      undeclared-mutability tools from G4 is the plan's own stated
      decision (§5.2), confirmed empirically this session (found live:
      a search call stuck PROPOSED forever with total silence).
    - settle_barrier_enabled + incomplete_turn_settle_enabled: every FDB
      tool call is a WRITE (same reasoning as above, via
      kernel/reducers.py._fix_step_kind), so G10/G11 already apply to
      every call -- "every tool call is irreversible" (§5.1) is mostly
      free. This adds the "wait longer if the turn sounds unfinished"
      extension (§5.3) on top of the existing settle wait.
    - max_read_retries=0, max_write_retries=0: FDB's mock APIs are
      deterministic and never fail transiently (docs/fdb_v3_
      implementation_plan.md §5.6, "No automatic retries under the
      profile") -- a retry would only ever be an extra logged call,
      which fails FDB's strict pass-rate check outright. Deliberately
      NOT the same budget as `max_interpret_retries` (config.py, still
      its own default of 2 here) -- that gates give-up on a *live
      INTERPRET call*, which can genuinely fail transiently, unlike
      FDB's own mock tools; found and fixed 2026-09-27 after a S2
      validation run silently lost an entire scenario (housing_13) to
      exactly this conflation.
    - multi_action_enabled: FDB-v3 scenarios routinely ask for 2-3
      actions in one turn (34 of 100 recordings) -- found live that 0 of
      those got their full tool set without this (Day 2 WP2).

    Day 2 S2 (win_plan_2026-09-27.md §6.2, the quick-win pack):
    - normalize_spoken_ids / strict_value_rules_enabled / clarify_
      reextract_enabled / speechlint_enabled: generic accuracy/quality
      fixes with no FDB-specific reasoning of their own -- on here simply
      because this is the profile FDB scenarios run under.
    - never_silent_unclear_enabled + turn_stall_salvage_ms=15000: the
      confirmed fix for the housing_11/housing_13 class of silent stall
      (Q4 -- read straight from their saved decision logs: interpretation
      resolved to a no-op act with no active goal, and nothing ever spoke
      for the rest of the scenario). 15s matches the plan's own number
      and sits comfortably above every real perceived-latency figure
      measured so far (S1's live LiveKit run: max 13.52s over 5
      recordings, 11.44s over 26) while still well inside FDB's own
      scoring timeout.
    - speculative_interpretation_enabled: Phase 6 built this but it was
      never turned on for FDB itself (Q9) -- saves the INTERPRET
      round-trip's latency on every turn.

    S3 (docs-personal/private-docs/s3_plan_2026-09-28.md; kill rule
    29 Sep 15:00 -- judge-scored full-100 A/B, kept only at >= +5 points):
    - action_plans_enabled: per-action interpretation compiled straight
      into the plan (kernel/action_plans.py) -- no PLAN job, no arguments
      lost between Interpreter and Planner, no "_2" slot collisions.
    - fill_unstated_required_enabled: FDB recordings are single-turn, so a
      clarifying question is never answered; a required number/yes-no the
      user didn't state is assumed and said aloud instead. Kept or dropped
      on its own A/B evidence, separately from action_plans_enabled.

    1 Oct audit:
    - vad_floor_enabled: the voice host's `user_speech` reports hold calls and
      speech while the user is speaking or their words are still being
      transcribed -- 29 calls went out mid-speech in the 72 current-code
      recordings. Not set in demo_config yet: without echo cancellation the
      agent's own voice reaches the microphone in a live room (see
      VoiceBridge.drop_foreign_segments) and would hold its next utterance;
      to be enabled there after a live check.

    - parallel_independent_actions (also in demo_config): a step no longer
      waits on an earlier one that is waiting for the user's answer, unless
      it reads that step's result -- an unanswered question no longer starves
      an independent action (class K).
    - partial_failure_continues (also in demo_config): one failed action no
      longer abandons the whole goal; actions that read its result fail with
      it, the rest still run, and the answer says what failed.

    `**overrides` lets a caller tune settle_ms/settle_ms_incomplete/etc.
    per run without editing this function -- passed straight to
    `dataclasses.replace`.
    """
    base = dataclasses.replace(
        DEFAULT_CONFIG,
        g4_exempt_undeclared_mutability=True,
        settle_barrier_enabled=True,
        settle_ms=1000,
        incomplete_turn_settle_enabled=True,
        settle_ms_incomplete=2500,
        max_read_retries=0,
        max_write_retries=0,
        multi_action_enabled=True,
        normalize_spoken_ids=True,
        strict_value_rules_enabled=True,
        never_silent_unclear_enabled=True,
        unclear_reask_delay_ms=2000,
        turn_stall_salvage_ms=15_000,
        clarify_reextract_enabled=True,
        speechlint_enabled=True,
        speculative_interpretation_enabled=True,
        echo_ack_enabled=True,
        action_plans_enabled=True,
        fill_unstated_required_enabled=True,
        assumed_value_settle_enabled=True,
        read_only_tools=FDB_READ_ONLY_TOOLS,
        settle_reads_enabled=True,
        conversational_replies_enabled=True,
        idle_replies_enabled=False,
        merge_split_turns_enabled=True,
        vad_floor_enabled=True,
        parallel_independent_actions=True,
        partial_failure_continues=True,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def demo_config(**overrides) -> Config:
    """The device-care extension's live-conversation profile.

    Same kernel and the same conversation mechanics as `fdb_v3_config`
    (action plans, split-turn merge, honest replies, speculative
    interpretation, echo ACK), with the differences a real conversation needs:

    - `vision_enabled`: the camera frame is analysed when a question about it
      opens (kernel/perception.py); the Gemini vision call reads the LED colour.
    - Tool mutability is declared by the toolset's manifest, so none of the
      benchmark's undeclared-mutability exemptions apply: a booking still needs
      the user's explicit intent (CommitGate G4) and waits out the settle
      barrier, so "make it Friday instead" cancels it before anything is booked.
    - `fill_unstated_required_enabled` is off: a booking with no date or slot
      is *asked about*, not assumed -- a real user can answer.
    - No whole-session watchdog (a conversation, not a scenario).
    """
    base = dataclasses.replace(
        DEFAULT_CONFIG,
        vision_enabled=True,
        settle_barrier_enabled=True,
        settle_ms=1000,
        incomplete_turn_settle_enabled=True,
        settle_ms_incomplete=2500,
        settle_reads_enabled=False,
        max_read_retries=0,
        max_write_retries=0,
        multi_action_enabled=True,
        normalize_spoken_ids=True,
        strict_value_rules_enabled=True,
        never_silent_unclear_enabled=True,
        unclear_reask_delay_ms=2000,
        turn_stall_salvage_ms=15_000,
        clarify_reextract_enabled=True,
        speechlint_enabled=True,
        speculative_interpretation_enabled=True,
        echo_ack_enabled=True,
        action_plans_enabled=True,
        fill_unstated_required_enabled=False,
        conversational_replies_enabled=True,
        merge_split_turns_enabled=True,
        parallel_independent_actions=True,
        partial_failure_continues=True,
        watchdog_timeout_ms=0,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


_TRUE_WORDS = {"true", "1", "yes", "on"}
_FALSE_WORDS = {"false", "0", "no", "off"}


def _coerce_override(key: str, raw: str):
    """Coerces one `--set key=value` string by the type of the field's own
    default in `DEFAULT_CONFIG` -- `dataclasses.fields(Config)[i].type` is
    a *string* here (`config.py` uses `from __future__ import
    annotations`), so it can't be used to coerce directly."""
    default = getattr(DEFAULT_CONFIG, key)
    text = raw.strip()
    if isinstance(default, tuple):
        # e.g. --set read_only_tools=search_flights,track_order (found by
        # review: fell through to a plain string, which the manifest code
        # then iterated character by character).
        return tuple(part.strip() for part in text.split(",") if part.strip())
    if isinstance(default, bool):
        lowered = text.lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        raise ValueError(f"--set {key}: expected a boolean, got {raw!r}")
    if isinstance(default, int):
        return int(text)
    if isinstance(default, float):
        return float(text)
    if default is None:
        if text.lower() == "none":
            return None
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
        return text
    return text


def apply_overrides(config: Config, pairs: list[str]) -> Config:
    """Applies `key=value` strings (e.g. from a script's repeatable
    `--set` option) on top of `config`. Unknown keys are rejected rather
    than silently ignored, so a typo can never make an A/B arm quietly
    identical to the other one."""
    if not pairs:
        return config
    known = {f.name for f in dataclasses.fields(Config)}
    changes: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--set expects key=value, got {pair!r}")
        if key not in known:
            raise ValueError(f"--set: unknown Config field {key!r}")
        changes[key] = _coerce_override(key, raw)
    return dataclasses.replace(config, **changes)
