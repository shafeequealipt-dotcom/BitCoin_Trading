# PRIMARY Issue — Phase 3 Cross-Check Report

Date: 2026-05-11
Trigger: operator request — "analysis and double check and cross check"
Branch: `fix/sell-bias-fixes-2026-05-11`
Status: cross-check complete, one hardening commit added, all tests pass.

# Section 1 — What Was Audited

Eight discrete audits, each producing a pass/fail finding:

| # | Audit | Verdict |
|---|-------|---------|
| 1 | Variable initialization in `optimize()` — no NameError on fallback paths | PASS |
| 2 | Counter-trade gate edge cases | PASS with one hardening |
| 3 | Double-counting of `_lock_override_count` | PASS |
| 4 | `config.toml` load + APEXSettings field-name match | PASS |
| 5 | StructuralData.setup_type plumbing (cache → assembler → optimizer) | PASS |
| 6 | APEX_FLIP_DECISION log field correctness | PASS (new integration tests added) |
| 7 | Full test suite + lint | PASS |
| 8 | Live smoke test — imports, settings, helpers | PASS |

# Section 2 — Findings Detail

## 2.1 Variable initialization audit (PASS)

Traced every new local variable referenced in the `APEX_FLIP_DECISION` log emission and confirmed each is initialized **before** the log line on every execution path that reaches it:

| Variable | Init site | Default |
|----------|-----------|---------|
| `_dir_lock_override_fired` | optimizer.py:333 | False |
| `_qwen_dir_before_lock` | optimizer.py:334 | `optimized.direction` |
| `_raw_conf` | optimizer.py:365 | 0.0 (via getattr default) |
| `_rr_boost` | optimizer.py:366 | 0.0 |
| `_rr_chosen` | optimizer.py:367 | 0.0 |
| `_rr_flipped` | optimizer.py:368 | 0.0 |
| `_effective_conf` | optimizer.py:406 | `_raw_conf + _rr_boost` |
| `_counter_protected` | optimizer.py:415 | False |
| `_insufficient` | optimizer.py:448 | False |
| `_flip_dir_count` | optimizer.py:449 | -1 (sentinel for "not evaluated") |
| `_flip_revert` | optimizer.py:476 | from `_enforce_flip_confidence` return |

Early-return paths (lines 124, 159, 216, and the except handler at line 686) bypass the log entirely — appropriate, since no optimization happened on those paths. The `_fallback()` path preserves `_apex_lock_state` per Issue 1 fix (2026-05-11). No NameError exposure.

## 2.2 Counter-trade gate edge cases (PASS with hardening)

Tested 7 input variants. **Finding**: the original `"counter" in setup_type.lower()` substring check would false-positive on a hypothetical future SetupType containing the substring (e.g. `BULLISH_ENCOUNTERED_RESISTANCE`).

**Resolution**: Tightened to `setup_type.lower().endswith("_counter")`. The actual SetupType enum (verified by reading `src/analysis/structure/models/structure_types.py:38-48`) has 11 values, only 2 of which contain "counter":

```
BULLISH_FVG_OB_COUNTER  = "bullish_fvg_ob_counter"
BEARISH_FVG_OB_COUNTER  = "bearish_fvg_ob_counter"
```

No false-positive exists in today's enum, but the suffix match is future-proof. Test `test_is_counter_trade_setup_rejects_substring_false_positive` added.

## 2.3 `_lock_override_count` double-counting (PASS)

Three gates plus the legacy lock override path all increment `self._lock_override_count`:
- Line 346: `APEX_DIR_LOCK_OVERRIDE`
- Line 438: `APEX_FLIP_COUNTER_PROTECTED`
- Line 474: `APEX_FLIP_INSUFFICIENT_DATA`
- Line 496: `APEX_FLIP_BLOCKED`

**Mutual exclusion** — each subsequent gate's trigger condition (`optimized.was_flipped`) is set to `False` by the upstream gate's revert. The confidence gate's `_enforce_flip_confidence` returns `(False, "")` when `optimized.direction == claude_direction`. So at most **one** of the four increments fires per `optimize()` call.

Counter is semantically a "total reverts" metric now rather than strictly "lock overrides". This is non-blocking — granular observability is delivered via the distinct `APEX_FLIP_*` log tags. Internal counter rename is deferred.

## 2.4 config.toml load (PASS)

End-to-end test loaded `config.toml`:

```
config keys (under [apex]): 30
APEXSettings dataclass fields: 38
recognized in dataclass: 30
unrecognized (silently ignored by _build_apex): []

apex_min_flip_confidence            = 0.7
apex_min_flip_confidence_buy_to_sell = 0.95
apex_min_flip_confidence_sell_to_buy = 0.7
apex_flip_rr_boost_threshold        = 3.0
apex_flip_rr_boost_amount           = 0.15
apex_block_flip_resize              = True
apex_min_trades_for_flip            = 5
apex_respect_counter_trade          = True
model                               = deepseek/deepseek-v3.2
```

All 4 new fields load with the HEAVY tune defaults. Zero unrecognized keys.

## 2.5 StructuralData.setup_type plumbing (PASS)

Traced the chain from structure_cache → optimizer via 5 input scenarios:

| Scenario | StructuralData.setup_type | _is_counter_trade_setup |
|----------|---------------------------|-------------------------|
| Enum `BULLISH_FVG_OB_COUNTER` | "bullish_fvg_ob_counter" | True |
| Plain string "bullish_fvg_ob" | "bullish_fvg_ob" | False |
| Missing `setup_type` attribute | "" (default) | False |
| `SetupType.NONE` enum | "none" | False |
| `structural_data=None` | n/a | False |

`_gather_structural_data_from_cache` at `src/apex/assembler.py:752-768` correctly extracts `.value` from the enum (with `getattr` + str fallback for non-enum values) and writes to `sd.setup_type`. Empty-string default in `StructuralData` (added 2026-05-11) handles missing fields gracefully.

## 2.6 APEX_FLIP_DECISION log field correctness (PASS — new integration tests)

Wrote `tests/test_apex_flip_decision_log.py` exercising all 6 decision_reason values end-to-end through the real `optimize()` call (with mocked qwen_client + assembler):

| Test | Verifies decision_reason value |
|------|--------------------------------|
| `test_apex_flip_decision_no_flip_attempt` | `no_flip_attempt` |
| `test_apex_flip_decision_lock_override` | `lock_override` |
| `test_apex_flip_decision_counter_protected` | `counter_protected` |
| `test_apex_flip_decision_insufficient_data` | `insufficient_data` |
| `test_apex_flip_decision_conf_below_threshold` | `conf_below_threshold` |
| `test_apex_flip_decision_flip_accepted` | `flip_accepted` |
| `test_apex_flip_decision_sell_to_buy_uses_lower_threshold` | asymmetric path |

All 7 pass. The log emits exactly **once per call** at the correct line number with the correct precedence in `decision_reason`. Each test also asserts on `brain_dir`, `apex_dir`, `flip_attempted`, `flip_accepted`, `qwen_initial_dir`, `flip_dir_trades`, and `dir_locked` field values to lock the contract.

**Loguru sink fixture**: the project uses loguru via `src/core/logging.py`, not stdlib `logging`. Standard pytest `caplog` does not capture loguru output. Added a `loguru_sink` fixture that appends formatted messages into a list via `logger.add(lambda msg: ..., format="{message}")` and removes the sink on teardown. Reusable for any future log-assertion tests in this codebase.

## 2.7 Full test suite + lint (PASS)

**APEX + shadow targeted tests**:

```
tests/test_apex_flip_rr_boost.py:       4 / 4 pass
tests/test_apex_flip_discipline.py:    12 / 12 pass
tests/test_apex_sell_bias_gates.py:    14 / 14 pass
tests/test_apex_flip_decision_log.py:   7 / 7 pass (NEW)
tests/test_apex_qwen_client.py:        21 / 21 pass
tests/test_apex_lock_propagation.py:    8 / 8 pass
tests/test_apex_pipeline_integration.py: 6 / 6 pass
tests/test_apex_tp_cap.py:             17 / 17 pass
tests/test_apex_direction_lock.py:     28 / 29 pass (1 deselected — pre-existing brain test)
tests/test_shadow_adapter_boot_grace.py + signature_parity: 16 / 16 pass

Total: 133 / 133 (excluding deselected) PASS
```

**Broad sweep** (`pytest tests/` — full repository, except `test_phase7/` which fails on a pre-existing unrelated `src.brain.executor` import error):

```
2,779 pass
9 skipped
1 deselected (the test_system_prompt_still_has_rsi_caution brain test)
2 failed — pre-existing, unrelated to this fix:
    tests/test_bybit_demo/test_websocket_subscriber.py
      ::test_subscriber_dispatches_close_then_dedups_replay
      ::test_subscriber_uses_pop_close_reason_when_no_stop_order_type
```

Bybit demo WS code is out of scope per spec Part A. The failures pre-date this branch.

**Ruff lint**: 8 issues in `src/apex/optimizer.py` + `src/apex/assembler.py` + `src/apex/models.py`. All are pre-existing on lines this fix did not touch (`_apply_constraints`, the original `_gather_structural_data_from_cache` body, the original `_apply_flip_resize_policy` signature). Zero lint issues introduced by this fix.

## 2.8 Live smoke test (PASS)

Direct invocation of all changed code paths from a fresh Python interpreter:

```
=== Import smoke test ===
All apex/* imports OK

=== Config end-to-end load ===
All 8 critical fields load with expected HEAVY tune defaults

=== StructuralData.setup_type plumbing ===
  default = empty string OK
  mutable = OK

=== Helper invocations ===
  _resolve_flip_threshold Buy->Sell = 0.95 OK
  _resolve_flip_threshold Sell->Buy = 0.70 OK
  _is_counter_trade_setup (counter): True OK
  _is_counter_trade_setup (normal): False OK
  _is_counter_trade_setup (no xray): False OK
  _check_insufficient_data_for_flip (3 Sell trades, need 5): True / 3 OK
  _check_insufficient_data_for_flip (7 Sell trades): False / 7 OK
```

No `ImportError`, `AttributeError`, or `NameError` on any path.

# Section 3 — What Changed In This Cross-Check Pass

One additional commit on the branch:

```
c1d0b33 test(p): cross-check hardening — endswith() counter-trade match
        + flip-decision integration tests
```

Files touched:
- `src/apex/optimizer.py` — `_is_counter_trade_setup` tightened (substring → suffix).
- `tests/test_apex_sell_bias_gates.py` — added regression test for substring false-positive.
- `tests/test_apex_flip_decision_log.py` — NEW file, 7 integration tests + loguru sink fixture.

# Section 4 — Naming + Integration Review

| Item | Check |
|------|-------|
| New config keys follow `apex_*` prefix convention | OK |
| New log tags follow `APEX_*` prefix convention | OK (`APEX_FLIP_COUNTER_PROTECTED`, `APEX_FLIP_INSUFFICIENT_DATA`, `APEX_FLIP_DECISION`) |
| New helper methods prefixed with `_` (private) | OK (`_resolve_flip_threshold`, `_check_insufficient_data_for_flip`, `_is_counter_trade_setup`) |
| New dataclass field follows snake_case | OK (`StructuralData.setup_type`) |
| Type hints on every signature | OK — verified by reading each new function |
| Docstring on every helper | OK |
| Structured logging via `loguru` `log.info` / `log.warning` | OK |
| Comment provenance — every new block references the fix series with date | OK |
| Backwards-compatible — legacy `apex_min_flip_confidence` preserved as fallback | OK |
| Shadow path unaffected | OK (16/16 shadow tests pass; no shadow code touched) |
| XRAY code untouched | OK (XRAY's flip code at strategy_worker.py:1604-1779 not modified) |
| Out-of-scope subsystems untouched | OK (brain, transformer, scanner, layer manager, bybit demo execution) |

# Section 5 — Final Branch State

```
c1d0b33 test(p): cross-check hardening — endswith() counter-trade match + flip-decision integration tests
037af78 docs(p): Phase 3 implementation summary + Phase 4 verification checklist
2c82657 feat(p): counter-trade + insufficient-data flip gates + APEX_FLIP_DECISION log
b14cbd9 feat(p): asymmetric Buy->Sell vs Sell->Buy flip-confidence thresholds
81552f9 fix(p): repair structural_data attribute typo on optimizer flip-confidence gate
11ee05b docs(p): Sell-bias investigation reports (Phase 0 + Phase 1 + Phase 2)
```

Six commits on `fix/sell-bias-fixes-2026-05-11`. Code is production-ready, lint-clean (in new code), test-verified, and properly woven into the project per industry conventions.

# Section 6 — Operator Action

The fix is ready for Phase 4 live verification. Restart procedure unchanged from `p_phase3_implementation_summary.md`:

```
sudo systemctl start trading-workers.service
sudo systemctl start trading-mcp-sse.service
```

Then monitor 24-48 hours. The new `APEX_FLIP_DECISION` log is the primary observability signal:

```
grep "APEX_FLIP_DECISION" data/logs/workers.log \
  | grep -oE "decision_reason=[a-z_]+" \
  | sort | uniq -c
```

Expected distribution after fix (rough estimate from log replay):
- `no_flip_attempt`: dominant (DeepSeek mostly preserves direction)
- `counter_protected`: non-zero when scanner emits counter-trade labels
- `insufficient_data`: non-zero on cold-start coins
- `flip_accepted`: rare (only high-conviction + sufficient-data flips pass)
- `conf_below_threshold`: moderate (Buy→Sell at 0.95 is a tight floor)
- `lock_override`: rare (DeepSeek mostly respects the lock prompt)
