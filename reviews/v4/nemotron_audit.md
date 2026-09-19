# Independent Read-Only Review — Janus (Samsung PRISM Theme 05)
**Date:** 2026-09-18  
**Target Version:** V4 (`v4-hardened`)  
**Reviewer:** Nemotron-3-Ultra (opencode)

---

## Executive Summary

**Overall:** V0–V4 implementation is genuinely impressive — 60/60 deterministic tests pass, kernel architecture faithfully implements single-writer coordination with read-set validation and same-step cancellation, versioning discipline is real. 8 kernel bugs caught by test-driven development proves the testing philosophy works.

**Critical gaps remain:** Two findings in `docs/post_v4_implementation_plan.md` (F1: zero audio support; F2: codec rejects unknown wire fields) are **confirmed and would cause total failure on hidden evaluation set**. Submission deliverables (deck, demo video, release tag) also missing (F3).

**Beyond documented gaps:** Additional issues found — latent race in write emission, incomplete S3 invariant check, several spec-mandated scenarios untested.

---

## PART 1: Verification of currentStatus.md & post_v4_implementation_plan.md Claims

| # | Claim | Status | Evidence |
|---|-------|--------|----------|
| 1 | V0–V4 implemented, tested, frozen (tags `v0-skeleton`–`v4-hardened`) | **VERIFIED** | `git tag -l` shows all 5 tags; 60/60 tests pass |
| 2 | Five V2 flags default `True` in `Config` | **VERIFIED** | `config.py:34–38` |
| 3 | Transitive invalidation (fixpoint loop, `derivation_read_set`) | **VERIFIED** | `kernel/invalidation.py:39–56`; test `test_i04_transitive_cancel_of_downstream_chain` |
| 4 | Settle barrier (G10/G11, `TimerWheel` liveness) | **VERIFIED** | `kernel/commit.py:84–99,155–164`; test `test_i14_correction_within_settle_window_blocks_the_original_booking` |
| 5 | Rebinder (`_can_rebind`, stays in `EXECUTING`) | **VERIFIED** | `kernel/interpret_apply.py:59–86,278–291`; test `test_i10_localized_correction_rebinds_without_replanning` |
| 6 | Absence read-sets (`PlanStep.absence_keys`, `ABSENT` entries) | **VERIFIED** | `model/types.py:379`; `kernel/executor.py:265–271`; test `test_i12_optional_constraint_addition_invalidates_via_absence_entry` |
| 7 | Claim grades + unconditional reconciliation | **VERIFIED** | `model/types.py:142–148`; `kernel/responder.py:147–150,238–268` |
| 8 | V3 multimodal (PerceptionScheduler, EvidenceStore, VisionAnalyzer) | **VERIFIED** | `kernel/perception.py`, `store/ledgers.py:299–379`, `workers/vision.py`; 6 V3 tests pass |
| 9 | Conflicts (HIGH perception vs user slot → CLARIFY with both values) | **VERIFIED** | `kernel/perception.py:212–230`; `kernel/responder.py:125–130` |
| 10 | `$Q` plan-binding substitution | **VERIFIED** | `kernel/executor.py:183–193`; test `test_m03_stale_frame_renewal` |
| 11 | C10 reference-bound write identifiers | **VERIFIED** | `kernel/executor.py:45–50,170–176,282–297`; 3 C10 tests |
| 12 | Targeted timing sweeps (±10ms, 5 offsets on I-03, I-14, R-05) | **VERIFIED** | `tests/test_v4.py:62–106,112–153,159–209` |
| 13 | Dockerfile builds & passes 60 tests on Python 3.11 | **VERIFIED** | `Dockerfile` exists; verified by maintainer |
| 14 | 8 real kernel bugs found & fixed via tests | **VERIFIED** | `currentStatus.md:83–88`; code changes match (e.g., `executor.py:79–112`) |
| 15 | TraceChecker false positives (S3, W1) fixed | **VERIFIED** | `sim/checker.py:113–128` (S3), `sim/checker.py:70–98` (W1) |
| 16 | **F1 — Zero audio support** (`audio_clip` raises `CodecError`) | **CONFIRMED** | `model/events.py:92–102` no `audio_clip`; `adapters/codec.py:46–47` raises; `entry.py:96` no try/except |
| 17 | **F2 — Codec rejects unknown payload fields** | **CONFIRMED** | `adapters/codec.py:64–68` raises `CodecError` on any extra field |
| 18 | **F3 — Submission deliverables missing** | **CONFIRMED** | No `docs/submission/`, no demo video, `PRISM_GENAI_HACKATHON_Y2026` tag not applied |
| 19 | **F4 — Watchdog never tested (T-07)** | **CONFIRMED** | `entry.py:80` creates watchdog; `SimHarness` never wires it; no test |
| 20 | **F5 — Quality multiplier: schema names leak into speech** | **PARTIALLY CONFIRMED** | `config/templates.py:13` → `CLARIFY_TEMPLATE` renders `flight_id`; V3 conflict clarify at `responder.py:130` uses `rsplit(".",1)[-1]` → "device_model" |

**Summary:** All implementation claims verified. Critical risks (F1–F5) confirmed. F5 slightly lower severity than stated due to `rsplit` extraction, but still leaks parameter-style names.

---

## PART 2: New Gaps Not in post_v4_implementation_plan.md

### 2.1 Spec-Mandated Scenarios Missing Coverage

| Finding | Source | Severity | Evidence |
|---------|--------|----------|----------|
| **S-07 (Unseen Tool) untested** — explicitly named in public suite | Theme_5_Guide.md §4 | **Blocks scoring entirely** | No test for planner handling tool not in manifest; `ToolCatalog` quarantine logic exists but unexercised |
| **P-02 (Malformed Input) — codec crash = scenario abort** | post_v4 F2; prompt1.txt §4.1 | **Blocks scoring entirely** | `adapters/codec.py:46–68` raises on unknown type/field; `entry.py:96` no try/except |
| **R-01, R-03, R-04, R-07, R-09 (Result Resilience) untested** | post_v4 §3.2; prompt1.txt §3.2 | **Degrades Task Completion** | Only R-02, R-06 tested. Out-of-order, late, partial, timeout results untested |
| **S-08 (Manifest Update mid-scenario) untested** | prompt1.txt §4.1 | **Degrades Task Completion** | `reducers.py:_apply_manifest` increments `catalog.version`; mechanism exists but no test |
| **I-02/I-06 (Correction after FINAL) untested** | prompt1.txt §1.6; post_v4 §3.2 | **Degrades Interruption Recovery** | Goal is `COMPLETED` after FINAL; user correction after not tested |
| **N-02b (Parallel reads + commit-last ordering) not implemented** | prompt3.txt C9 | **Degrades Safety & Protocol** | `executor.py` emits writes as soon as deps met; no deferral until independent reads consumed |
| **M-02 (Partial transcript / inert tail → C1) untested** | post_v4 §3.2 | **Degrades Response Latency** | Inert-tail promotion (C1) not built; `detector.py:77–78` stub unused |
| **M-04, M-05, M-09 (Multimodal variants) untested** | post_v4 §3.2 | **Degrades Task Completion (multimodal)** | Only N-05, M-03, M-06, M-07, M-08 tested |
| **T-03, T-04, T-06 (Timing/meta) untested** | post_v4 §3.2 | **Degrades Response Latency / IR** | Long utterance, rapid chunks, speculative latency untested |
| **T-07 (Watchdog) untested** | post_v4 F4 | **Blocks scoring if 120s hit** | `entry.py:80` creates `ScenarioWatchdog`; `SimHarness` never wires it |

### 2.2 Architecture Invariants Not Fully Implemented

| Finding | Source | Severity | Evidence |
|---------|--------|----------|----------|
| **C9 Commit-last ordering (CS-31) not implemented** | prompt2.txt §7.3; prompt3.txt C9 | **Degrades Safety (10%) + IR (35%)** | `executor.py:59–148` proposes calls in plan order; no read/write emission separation |
| **C1 Inert-tail promotion not built** | prompt3.txt C1 | **Degrades Response Latency (15%)** | `detector.py:77–78` stub exists; not wired in `reducers.py` or `task.py` |
| **C2 Response frames (typed holes) not built** | prompt3.txt C2 | **Degrades Task Completion (40%) + Quality** | `Composer` only FINAL path; no event-anchored frame rendering |
| **C8(a) Two-phase emission not implemented** | prompt3.txt C8 | **Fixes defect L3** | `emission.py:67–109` validates/writes per-action, not batch-validate-then-write |
| **Full 34-invariant TraceChecker (CS-01…CS-34) not implemented** | prompt2.txt §17; blueprint.md | **Degrades Safety (10%) + Tech Depth (25%)** | Only P1, P3, W1, S3 (partial), C1, static no-wallclock implemented |
| **PolicyRegistry not implemented (flat Config used)** | prompt2.txt §20 | **Architecture deviation** | `config.py` uses flat `Config` with 20+ flags |

### 2.3 Input/Output Contract Gaps

| Finding | Source | Severity | Evidence |
|---------|--------|----------|----------|
| **No `audio_clip` event support** | Theme_5_Guide.md §3.1 | **Blocks 30% of scenarios** | `model/events.py:92–102` — no `AudioClipPayload`; `PAYLOAD_TYPES` lacks it |
| **No ASR worker** | Theme_5_Guide.md §3.2(5) | **Blocks audio scenarios** | `workers/asr.py` — CUT; `JobKind.ASR` not in enum |
| **Snapshot may include DERIVED facts (perception) stale if lease expired** | Theme_5_Guide.md §3.1; prompt2.txt §12 | **Degrades Task Completion (40%)** | `emission.py:158–179` uses `snapshot_committed()` which includes `DERIVED` |
| **No `PROGRESS`/`HOLD` utterance kinds** | Theme_5_Guide.md §3.1 | **Degrades Response Latency (15%) + Quality** | `responder.py:3–9` explicitly notes not implemented |

---

## PART 3: Code-Level Bugs, Races, Invariant Violations

### 3.1 Critical: Write Emission Race (Violates P1 + L3 Defect)

**Finding:** SPEAK (ACK) can claim IN_PROGRESS for a TOOL_CALL not yet emitted — or that fails EmissionGate validation later in same batch.

- **Location:** `kernel/emission.py:67–109` (per-action validate-then-write loop)
- **Root cause:** Emission is **per-action**, not two-phase. Loop validates action *i*, writes it, then validates *i+1*. SPEAK with `claim_grade=IN_PROGRESS` referencing call `c1` passes if `c1` is `PROPOSED`. TOOL_CALL for `c1` comes later (CANCEL → SPEAK → TOOL_CALL). If TOOL_CALL then fails validation, SPEAK already emitted with false claim.
- **Spec violation:** `prompt2.txt §5.2` emission order puts SPEAK before TOOL_CALL. `prompt3.txt L3`: "ACK references a call that does not exist yet... If relaxed and call then failed, ACK would be a false claim."
- **Trigger:** Multiple TOOL_CALLs + SPEAK in same step where later TOOL_CALL fails validation.
- **Test gap:** No test emits multiple TOOL_CALLs + SPEAK where later TOOL_CALL fails.

### 3.2 High: S3 Invariant Check Unsound (False Confidence)

**Finding:** `TraceChecker._check_s3` only verifies snapshot digest non-empty, **not that it matches state as of that step**.

- **Location:** `sim/checker.py:113–128`
- **Code comment admits:** "A full S3 check... needs per-step FactStore history... comparing against final post-run state instead is unsound"
- **Impact:** S3 (snapshot accuracy at emission) is core to Task Completion + IR = 75% weight. Checker gives false confidence.
- **Fix:** `DecisionLogger` must record `FactStore` revision/snapshot per step, or `SnapshotProjector` embed projected facts.

### 3.3 High: Carried-Over Calls May Lack `goal.active` in Read Set

**Finding:** `executor.py:168` adds `goal.active` + `catalog.version` to **new** calls only. Carried-over CONSUMED calls (`executor.py:90–100`) retain original read set which may lack these keys.

- **Location:** `kernel/executor.py:90–100` vs `168`
- **Impact:** Carried-over call not invalidated when goal replaced (new goal → `goal.active` changes). Violates `prompt2.txt §3.4`: "Coarse generation: goal.active. Every piece of goal-bound work includes it."
- **CurrentStatus.md bug #4 claims fixed** — but fix only for new calls, not carried-over.
- **Trigger:** Goal g1 → search completes → g2 replaces g1 → RETURN_TO_GOAL g1 → carried-over call lacks `goal.active` in read set → not re-validated.

### 3.4 Medium: `PlanExecutor._bind` STEP_OUTPUT Read Set Inconsistency

**Finding:** When `transitive_invalidation=True` and binding maps to derived fact, derived key added to `read_keys`. But `read_set` built at line 273 uses `sorted(set(read_keys))` — if same key added earlier via FACT binding, version/digest comes from first occurrence.

- **Location:** `kernel/executor.py:214–273`
- **Note:** Comment at line 228 says "falls back to V1 path... no matching output_map". If output_map exists but derived fact RETRACTED (lines 234–236), returns `None, derived_key` → triggers `_ask_for` clarification instead of fallback. Correct behavior but comment misleading.

### 3.5 Medium: Interpretation Proposal Read Set Not Re-validated at EOT

**Finding:** `reducers.py:211–212` validates proposal read_set at WORKER_RESULT. But proposal includes `turn.{turn_id}.prefix` digest captured at dispatch. If chunks arrive between WORKER_RESULT and EOT, digest changes but proposal applied anyway at EOT without re-check.

- **Location:** `reducers.py:130–168` (EOT handling), `workers/interpreter.py:100–108` (read_keys includes `turn.{turn_id}.prefix`)
- **Fix:** At EOT, re-validate proposal read_set against current facts before applying.

### 3.6 Low: `TimerWheel` Liveness Fire Not Exercised in Tests

**Finding:** `commit.py:155–164` schedules liveness timer for settle barrier. `sim/checker.py` has no check for `liveness_fire: true`.

- **Impact:** Liveness rule (prompt2.txt §13.3) implemented but unverified. Test `test_t05_settle_timer_liveness_no_deadlock` advances clock manually, not via `TimerFiredPayload`.

### 3.7 Low: `video_frame` capture_ts_us Uses Processing Time

**Finding:** `reducers.py:178–182` creates `Observation(capture_ts_us=now_us)`. Harness sends frame with its timestamp (envelope `ts_us`), but reducer uses kernel's `now_us` (step clock time).

- **Impact:** For `AT_UTTERANCE` alignment (`perception.py:177–183`), frame's `capture_ts_us` should be harness capture time. Correct in current harness (driver advances clock before `step()`) but would diverge if kernel batches frames.

### 3.8 Low: Provider Protocol Text-Only — Vision/ASR Pass References Only

**Finding:** `workers/gateway.py:22–23` Protocol only has `complete_json(kind, prompt, schema)`. `workers/vision.py` passes `obs_id` by reference; `ScriptedProvider` mocks responses.

- **Status:** Documented in `currentStatus.md:120–121`. `post_v4_implementation_plan.md Phase 3` plans optional `media` arg.

---

## Consolidated Findings Ranked by Severity

### 🔴 BLOCKS SCORING ENTIRELY (would zero evaluation)

| ID | Finding | Source | Why It Blocks |
|----|---------|--------|---------------|
| **B1** | **Zero audio support** — `audio_clip` raises `CodecError` | Theme_5_Guide.md §3.1, §4 (30% audio) | 30% of public+hidden scenarios audio; first audio event crashes |
| **B2** | **Codec rejects unknown wire fields** — any extra field crashes | Theme_5_Guide.md §4 (kit unreleased) | Kit format is guess; single field mismatch = total failure |
| **B3** | **Submission deliverables missing** — no deck, demo video, tag | Samsung_PRISM...md §25 | Stated disqualification condition |
| **B4** | **Watchdog never tested (T-07)** — 120s cap, no fallback output | Theme_5_Guide.md §6 | Scenario hangs → no FINAL → 0 score |
| **B5** | **S-07 (Unseen Tool) untested** — explicitly in public suite | Theme_5_Guide.md §4 | Public suite scenario class; no test |

### 🟠 DEGRADES SPECIFIC SCORING CATEGORY SIGNIFICANTLY

| ID | Finding | Category | Weight | Why |
|----|---------|----------|--------|-----|
| **D1** | **Commit-last ordering (C9) not implemented** | Safety (10%) + IR (35%) | 45% | Writes execute while independent reads in flight; correction cancels write → reconciliation/double-book |
| **D2** | **Two-phase emission (C8a) not implemented** | Safety (10%) + Quality | ~20% | L3 defect: SPEAK claims IN_PROGRESS for unemitted call, or call then fails |
| **D3** | **S3 TraceChecker unsound** | Task Completion (40%) + IR (35%) | 75% | Snapshot accuracy core to 3/4 categories; checker gives false confidence |
| **D4** | **Carried-over calls lack `goal.active` in read set** | IR (35%) + Task Completion (40%) | 75% | Goal replacement doesn't invalidate carried-over calls |
| **D5** | **No `PROGRESS`/`HOLD` utterance kinds** | Response Latency (15%) + Quality | ~20% | Spec requires "progress narration"; only ACK implemented |
| **D6** | **C1 Inert-tail promotion not built** | Response Latency (15%) | 15% | Exact-digest promotion fails on fillers → full re-interpretation latency |
| **D7** | **C2 Response frames not built** | Task Completion (40%) + Quality | ~45% | FINAL waits for Composer, not event-anchored |

### 🟡 DEGRADES SCENARIO CLASS / EDGE CASE

| ID | Finding | Category | Scenario |
|----|---------|----------|----------|
| **E1** | Interpretation proposal read set not re-validated at EOT | IR | Rapid chunks during interpretation |
| **E2** | R-01, R-03, R-04, R-07, R-09 untested | Task Completion | Out-of-order, late, partial, timeout results |
| **E3** | S-08 (manifest update mid-scenario) untested | Task Completion | Tool catalog changes during execution |
| **E4** | I-02/I-06 (correction after FINAL) untested | IR | User corrects slot after task "complete" |
| **E5** | M-04, M-05, M-09 (multimodal variants) untested | Task Completion (1.5×) | Multi-frame, ASR+vision, frame-grounded write |
| **E6** | T-03, T-04, T-06 (timing) untested | Response Latency | Adversarial timing edge cases |
| **E7** | `video_frame` capture_ts_us uses processing time | Multimodal | Frame alignment if kernel batches frames |
| **E8** | 28/34 TraceChecker invariants unchecked | Safety + Tech Depth | CS-01…CS-34 from blueprint |

### 🟢 CODE QUALITY (not directly scored)

| ID | Finding |
|----|---------|
| **Q1** | `Provider` protocol text-only — vision/ASR pass references only |
| **Q2** | Flat `Config` vs spec's `PolicyRegistry` — acceptable but diverges |
| **Q3** | `detector.py:77–78` `is_inert_tail` stub — dead code until C1 wired |
| **Q4** | `responder.py:3–9` PROGRESS/HOLD "not implemented" — honest but incomplete |

---

## Suggested Test Scenarios (Style of `test_v1.py`/`test_v2.py`)

### For B1 (Audio — Critical Path)
```python
def test_audio_only_request_completes_task():
    """N-06: audio-only request → ASR → synthetic chunks → task completion."""
    config = v3_config(asr_enabled=True)
    provider = ScriptedProvider()
    provider.register("asr", "hello", {"segments": [{"text": "Find flights to Delhi", "offset_us": 0, "end_us": 1_000_000}], "end_of_utterance": True})
    provider.register("interpret", "Find flights to Delhi", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Delhi.", "claims": ["result:s1"]})
    
    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [{"type": "audio_clip", "payload": {"clip_id": "a1", "format": "wav", "duration_ms": 1000}}])
    actions = drain(h, 3_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
```

### For B2 (Codec Tolerance — Critical Path)
```python
def test_codec_tolerates_unknown_fields_and_types():
    """P-02: unknown event type / extra fields / missing optional fields must not crash."""
    config = Config()
    h = SimHarness(config, seed=1)
    for raw in [
        {"ts_us": 100_000, "type": "unknown_type", "payload": {}},
        {"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "hi", "confidence": 0.9, "speaker": "user"}},
        {"ts_us": 100_000, "type": "end_of_turn", "payload": {}},
    ]:
        envelopes = h.codec.decode(raw, seq=h._next_seq())  # must not raise
        report = h.kernel.step(envelopes)
    assert_clean(h)
```

### For D1 (Commit-Last Ordering)
```python
def test_commit_last_ordering_write_deferred_until_independent_reads_consumed():
    """N-02b: write must not emit before unrelated read completes."""
    provider = ScriptedProvider()
    provider.register("interpret", "Book flight and check weather", 
        {"act": "new_goal", "intent": "book_and_check", "slot_deltas": [
            {"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"},
            {"name": "city", "scope": "goal", "op": "set", "value": "Delhi"}
        ], "commit_intent": True})
    provider.register("plan", "book_and_check", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
            {"local_id": "s2", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []},
            {"local_id": "s3", "tool": "check_weather", "kind": "read", "bindings": {"city": {"type": "fact", "key": "slot.$G.city"}}, "after": []},
        ]
    })
    provider.register("compose", "done", {"text": "Done.", "claims": ["result:s1", "effect:s2", "result:s3"]})
    
    config = v2_config(settle_barrier_enabled=True)
    h = new_harness(config, tools={
        "search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}},
        "book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}},
        "check_weather": {"latency_ms": 800, "response": {"temp": "30C"}},
    }, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, BOOK_FLIGHT_TOOL, CHECK_WEATHER_TOOL])])
    h.send(100_000, [chunk_event("Book flight and check weather")])
    h.send(150_000, [eot_event()])
    # book_flight (write) must NOT emit until check_weather (read) consumes
    actions = drain(h, 3_000_000, stop_on_final=False)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    # Current: search_flights, book_flight, check_weather
    # Commit-last: search_flights, check_weather, (wait), book_flight
```

### For D2 (Two-Phase Emission)
```python
def test_two_phase_emission_speak_never_claims_in_progress_for_unemitted_call():
    """L3: SPEAK with IN_PROGRESS must not emit if referenced TOOL_CALL fails validation in same batch."""
    # Construct step where FastResponder emits PROGRESS (IN_PROGRESS) for call c1,
    # but c1's TOOL_CALL fails schema validation at emission time.
    # Current per-action emission: PROGRESS validates (c1 PROPOSED, read_set valid) → emitted.
    # Then TOOL_CALL for c1 validates → fails schema → rejected.
    # PROGRESS already emitted with false IN_PROGRESS claim.
```

### For D3 (S3 Sound Check)
```python
def test_s3_snapshot_matches_state_at_emission_step():
    """S3: every snapshot-bearing action's snapshot equals fresh projection of facts AS OF THAT STEP."""
    # Requires DecisionLogger to record per-step fact state, or SnapshotProjector to embed projected facts.
    # Current checker only validates digest non-empty.
```

### For D4 (Carried-Over Call Missing goal.active)
```python
def test_goal_return_reuses_retained_result_with_valid_read_set_including_goal_active():
    """Return to suspended goal: retained result's read set must include goal.active to be valid."""
    # 1. Goal g1, search_flights completes, call c1 CONSUMED (read_set lacks goal.active — V0 bug)
    # 2. NEW_GOAL g2 → goal.active = g2
    # 3. RETURN_TO_GOAL g1 → goal.active = g1
    # 4. PlanExecutor carries over c1 (CONSUMED, read_set valid because goal.active not in it)
    # 5. Step marked DONE without ever checking goal.active changed twice
    # Expected: c1 should be invalidated at step 2 and re-run at step 4
```

---

## Recommended Priority Order

1. **Phase 0** (post_v4 §107): Fix F2 (codec tolerance) + F3 (submission deliverables) — **do first, removes disqualification/total-failure risk**
2. **Phase 1** (post_v4 §118): Fix F1 (audio ingestion) + F4 (watchdog test) + P-02 (malformed input) — **kit-readiness hardening**
3. **Phase 2** (post_v4 §128): Audio path (ASR worker, dedupe) — **unlocks 30% of scenarios**
4. **Fix D3 (S3 checker):** `SnapshotProjector` record projected facts per step; update `TraceChecker._check_s3`
5. **Fix D4 (goal.active in carried-over calls):** In `executor.py:90–100`, rebuild read set with current `goal.active` + `catalog.version`
6. **Fix D1 (commit-last):** In `PlanExecutor.decide`, partition ready steps into reads/writes; emit all reads first; propose writes after independent reads CONSUMED
7. **Fix D2 (two-phase emission):** `EmissionGate.emit` → validate all intended actions first, then write all admitted in order
8. **Remaining:** C1, C2, full TraceChecker, unseen tool test, manifest update test, post-FINAL correction test

---

**Bottom line:** Kernel is architecturally sound and well-tested for what it covers. **Submission-blocking issues (B1–B5)** are real and must be addressed before 25 Sep. **Scoring-degrading gaps (D1–D7)** are where hidden evaluation will differentiate — commit-last ordering and two-phase emission are highest-leverage fixes for Safety (10%) and Interruption Recovery (35%).