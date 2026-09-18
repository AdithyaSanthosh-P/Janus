# Plugging Janus into a harness

`src/prism_rt/entry.py` is the composition root. Everything below is real
and exercised by `tests/test_integration.py` and `demo/run_queue_harness.py`
— not aspirational.

## The contract

`guidelines/Theme_5_Guide.md` §3 states the interface as *"two asynchronous
queues (timestamped events input, actions output)"*. That's exactly what
`Runtime.run_scenario` consumes:

```python
from prism_rt import entry
from prism_rt.config import Config
from prism_rt.workers.gateway import GeminiProvider  # or any Provider

runtime = entry.setup(Config(), provider=GeminiProvider())

events: asyncio.Queue[dict | None] = asyncio.Queue()
actions: asyncio.Queue[dict] = asyncio.Queue()

summary = await runtime.run_scenario(events, actions, meta={"seed": 7})
```

- Push raw wire-format event dicts onto `events` as they arrive. Push
  `None` (or any falsy value) to end the scenario.
- Read encoded action dicts off `actions` as they're produced — the loop
  pushes them the moment `EmissionGate` emits them, same step.
- `run_scenario` returns once `events` yields a falsy sentinel, or the
  120-second-class watchdog fires (`Config.watchdog_timeout_ms`, default
  105s) — either way you get a `RunSummary` back.

If your harness instead hands you a blocking `read_event()`/`write_action()`
pair (a callback style, not raw queues), use the adapter instead:

```python
class MyHarnessIO:
    def read_event(self) -> dict | None: ...   # blocks until the next event, or returns None
    def write_action(self, encoded: dict) -> None: ...

summary = await runtime.run_scenario_io(MyHarnessIO())
```

This bridges the blocking calls onto the same async path via
`asyncio.to_thread`, so they never stall the loop that needs to keep
driving worker tasks forward.

## Event JSON shape

Canonical shape (`adapters/codec.py`):

```json
{"ts_us": 123456, "type": "text_chunk", "payload": {"text": "..."}}
```

`decode()` is deliberately tolerant, not strict — our guessed schema is a
guess, and the real kit's format is unreleased. Specifically:

| Variant | Accepted alongside the canonical form |
|---|---|
| Timestamp | `ts_us` (native), or `ts_ms`/`timestamp`/`ts` (treated as milliseconds) |
| Payload nesting | `{"payload": {...}}`, or a flat shape (fields alongside `type`/`ts_us`) |
| Event type | `chunk`→`text_chunk`, `eot`/`end_turn`→`end_of_turn`, `interrupt`→`interruption`, `frame`→`video_frame`, `audio`→`audio_clip` |
| Field names | `id`→`call_id`/`job_id`/`frame_id`/`clip_id` (per event type), `content`/`message`→`text`, `manifest`→`tools` |
| Unknown fields | Ignored, never rejected |
| Unknown event types | Ignored (`decode` returns `[]`), never raised |
| Wrong-typed required field | Ignored (e.g. `text: 12345` — a required `str` field holding a non-string value drops the event rather than crashing downstream) |

Anything genuinely unparseable is dropped, not raised — a malformed or
unrecognized event should never end a scenario (P-02). If you know the
real kit's exact schema, the whole translation lives in `decode`/`encode`
in this one file; nothing else in the codebase needs to change (`docs/
prompt 2.txt` §2.1: "the only place wire field names appear").

## Event types this project understands

`manifest`, `text_chunk`, `interruption`, `end_of_turn`, `tool_result`,
`worker_result` (internal — a real harness never sends this), `video_frame`,
`audio_clip` (accepted, stored as an Observation; ASR analysis isn't built
yet), `timer_fired` (internal liveness hint), `watchdog` (internal — see
below).

## Action JSON shape

```json
{"action_id": "a-0001", "action_type": "speak", "ts_us": 123456,
 "trigger_event_id": "...", "rule_id": "...", "body": {...},
 "snapshot": {"intent": "...", "slots": {...}, "revision": 3, "digest": "..."}}
```

`action_type` is one of `speak`, `tool_call`, `cancel`, `clarify`, `final`.
`snapshot` is present only on actions that carry a state snapshot (FINAL,
and others per policy). `encode()` stays strict — it's our own output,
fully under our control.

## The watchdog

If the wall-clock budget (`Config.watchdog_timeout_ms`) elapses before the
scenario naturally ends, `run_scenario` synthesizes a `watchdog` event and
runs it through the kernel like any other. This triggers `docs/prompt
2.txt` §8.9's salvage behavior deterministically: every in-flight READ is
cancelled, no further WRITE is ever admitted, and if the active goal
hasn't produced a FINAL yet, a templated one is forced —
`task_completed: false`, always, since the entire point is to never claim
completion it didn't reach. This is directly testable (`tests/
test_integration.py`'s `test_t07_*` cases) by sending a `watchdog` event
yourself; you don't need to actually wait out the real timeout to verify
the salvage path.

## What's NOT handled yet

- **ASR** — `audio_clip` is stored, not transcribed. Phase 2 of `docs/
  post_v4_implementation_plan.md`.
- **Live pixel/audio data** — `workers/vision.py` and any future ASR
  worker reference frames/clips by id only; the `Provider` protocol is
  text-only. Phase 3 of the same plan.
- **The real kit's actual schema** — still unreleased as of this writing.
  Everything above is our best-effort guess, hardened to survive being
  wrong rather than confirmed to be right.
