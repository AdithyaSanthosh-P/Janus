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
      which fails FDB's strict pass-rate check outright.

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
    )
    return dataclasses.replace(base, **overrides) if overrides else base
