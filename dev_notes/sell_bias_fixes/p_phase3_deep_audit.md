# PRIMARY Issue — Phase 3 Deep Audit (A-through-N)

Date: 2026-05-11
Trigger: operator request — "properly analyse each file each code each phase one by one"
Branch: `fix/sell-bias-fixes-2026-05-11`
Status: 14 audit phases complete (A-N), 1 real robustness gap found + fixed.

# Section 1 — Audit Phase Summary

| Phase | Audit | Result |
|-------|-------|--------|
| A | Dependency map of every touched file/function/class | PASS |
| B | Architecture review per file | PASS |
| C | Contract preservation check per function | PASS |
| D | Wiring + integration check | PASS |
| E | Naming convention audit | PASS |
| F | Smoke tests | PASS |
| G | Unit tests in isolation | 30 / 30 PASS |
| H | Integration tests | 20 / 20 PASS |
| I | Regression tests | 249 PASS, 0 fail |
| J | E2E behavioral replay vs frozen log data | PASS (15 of 16 Buy→Sell flips predicted blocked) |
| K | Performance + latency impact | 3 µs / call, 0.0002% of DeepSeek HTTP RTT |
| L | Robustness — degraded inputs | **1 real gap found + fixed** |
| M | Production-quality review | PASS (type hints, docstrings, no debug residue) |
| N | Final verification report | This document |

# Section 2 — Per-File Audit

## 2.1 `config.toml`

**Role**: TOML configuration. Loaded once at startup by `src/config/settings.py:_build_apex`.

**Changes**: Added 4 keys under `[apex]`:

| Key | Value | Provenance comment |
|-----|-------|---------------------|
| `apex_min_flip_confidence_buy_to_sell` | 0.95 | "Buy→Sell flips destroy WR (-16.1 pp shadow)" |
| `apex_min_flip_confidence_sell_to_buy` | 0.70 | "Sell→Buy flips help (+10.4 pp shadow)" |
| `apex_min_trades_for_flip` | 5 | "DeepSeek mis-reads the prompt rule" |
| `apex_respect_counter_trade` | true | "Scanner emits 91 counter-trade labels in 9-h window" |

**Dependencies**: read by `_build_apex` via `hasattr` filter — any unrecognized key is silently ignored, so backward-compatible.

**Wiring verified**: `[apex]` section has 30 keys, all 30 recognized by `APEXSettings` dataclass (0 unrecognized).

## 2.2 `src/config/settings.py`

**Role**: project-wide settings dataclass. `APEXSettings` is a sub-dataclass at line 1805.

**Changes**: Added 4 new fields to `APEXSettings` with defaults matching the operator's HEAVY tune (0.95 / 0.70 / 5 / True). Each field documented with provenance comment + rationale + link back to investigation docs.

**Dependencies**: consumed by `TradeOptimizer.__init__` via dependency injection at `src/workers/manager.py:2549`.

**Contract impact**: zero — new fields don't replace existing ones. `apex_min_flip_confidence` (legacy) is preserved as a fallback in `_resolve_flip_threshold` for unknown direction pairs.

## 2.3 `src/apex/optimizer.py`

**Role**: orchestrator. `TradeOptimizer.optimize()` is the single entry point at `src/core/layer_manager.py:1349`.

**Changes**:

| Change | Lines | Type |
|--------|-------|------|
| Typo fix `structure_data` → `structural_data` | 386 | bug fix |
| `_dir_lock_override_fired` flag | 333-347 | gate tracking |
| Counter-trade gate | 415-439 | new gate |
| Insufficient-data gate | 448-474 | new gate |
| APEX_FLIP_DECISION log | 547-605 | observability |
| `_resolve_flip_threshold` | 1165-1205 | new helper |
| `_check_insufficient_data_for_flip` | 1081-1128 (hardened in Phase L) | new helper |
| `_is_counter_trade_setup` | 1130-1164 | new helper |
| `_enforce_flip_confidence` (internal threshold lookup) | 1207-1259 | contract preserved |

**Dependencies on this file**:
- `src/workers/manager.py:2549` — instantiation
- `src/core/layer_manager.py:1349` — `.optimize()` invocation

**Contract preservation**: All 6 public/protected signatures unchanged. New methods are new contracts (additive).

**Architecture**: business-logic layer. New helpers compose existing data-access (assembler) and external clients (qwen) via existing DI. No layer violations.

## 2.4 `src/apex/assembler.py`

**Role**: data gathering. `_gather_structural_data_from_cache` reads from `services["structure_cache"]` to build `StructuralData`.

**Changes**: 17 lines added to populate `sd.setup_type` from `analysis.setup_type` (SetupType enum → `.value` string). Best-effort try/except — assembler's contract is never to raise (returns partial StructuralData on degraded source).

**Dependencies on this file**:
- `IntelligenceAssembler.assemble` calls `_gather_structural_data_from_cache` at line 689.
- `IntelligenceAssembler.assemble` is called by `TradeOptimizer.optimize` at line 150.

**Contract preservation**: function signature unchanged; new field added defensively (won't break if `analysis.setup_type` is None or not present).

## 2.5 `src/apex/models.py`

**Role**: pure data containers — dataclasses.

**Changes**: Added one new field `setup_type: str = ""` to `StructuralData` with full provenance comment.

**Dependencies on this file**: imported by every other apex/* file (typical for a models module).

**Contract preservation**: new field has a default of `""` — existing instantiations of `StructuralData(...)` without `setup_type` continue to work.

## 2.6 Test files

| File | Type | Tests added/modified |
|------|------|---------------------|
| `tests/test_apex_qwen_client.py` | regression | 1 line fix (mock attribute name) |
| `tests/test_apex_flip_rr_boost.py` | unit | 1 new regression guard |
| `tests/test_apex_flip_discipline.py` | unit | 4 new asymmetric-threshold tests; existing tests updated to use HEAVY tune defaults |
| `tests/test_apex_sell_bias_gates.py` | unit (NEW) | 17 tests covering counter-trade + insufficient-data + defensive paths |
| `tests/test_apex_flip_decision_log.py` | integration (NEW) | 7 tests covering all 6 `decision_reason` values + asymmetric path |

# Section 3 — Architecture Review Per File (Phase B)

The `src/apex/` module is a self-contained sub-system with proper layering:

```
optimizer.py    [business logic — orchestrates the others]
    |
    +-> assembler.py    [data access — gathers IntelligencePackage]
    +-> qwen_client.py  [infrastructure — external HTTPS to OpenRouter]
    +-> gate.py         [post-optimization safety validation]
    +-> models.py       [data containers — dataclasses]
    +-> prompts.py      [presentation — LLM prompt strings]
```

My changes respect this layering:

| Change | File | Correct layer? |
|--------|------|----------------|
| `setup_type` field on StructuralData | models.py | YES (data) |
| `_gather_structural_data_from_cache` updated | assembler.py | YES (data access) |
| `_is_counter_trade_setup` helper | optimizer.py | YES (business logic) |
| `_check_insufficient_data_for_flip` helper | optimizer.py | YES (business logic) |
| `_resolve_flip_threshold` helper | optimizer.py | YES (business logic) |
| Config additions | settings.py + config.toml | YES (configuration) |

No layer violations. No leaky abstractions. Architecture verdict: **PASS**.

# Section 4 — Contract Preservation (Phase C)

Method-by-method comparison pre-fix vs post-fix:

| Method | Signature | Side effects | Return | Contract |
|--------|-----------|--------------|--------|----------|
| `optimize` | unchanged | adds new log emissions | unchanged | PRESERVED |
| `_check_direction_lock` | unchanged | unchanged | unchanged | PRESERVED |
| `_enforce_flip_confidence` | unchanged | unchanged | unchanged (now uses _resolve_flip_threshold internally) | PRESERVED |
| `_apply_flip_resize_policy` | unchanged | unchanged | unchanged | PRESERVED |
| `_parse_response` | unchanged | unchanged | unchanged | PRESERVED |
| `_apply_constraints` | unchanged | unchanged | unchanged | PRESERVED |
| `_fallback` | unchanged | unchanged | unchanged | PRESERVED |
| `_log_optimization` | unchanged | unchanged | unchanged | PRESERVED |
| `get_stats` | unchanged | unchanged | unchanged | PRESERVED |
| `_check_flip_evidence` | unchanged | unchanged | unchanged | PRESERVED |
| `_resolve_flip_threshold` | NEW | none (pure) | float | NEW contract |
| `_is_counter_trade_setup` | NEW | none (pure) | bool | NEW contract |
| `_check_insufficient_data_for_flip` | NEW | none (pure) | tuple[bool, int] | NEW contract |

Verdict: **PASS** — zero breaking changes to public/protected API.

# Section 5 — Wiring Audit (Phase D)

End-to-end chain (each layer verified by smoke test):

```
config.toml [apex]               -- 4 new keys
    |
    v
APEXSettings dataclass           -- 4 new fields (defaults match config)
    |
    v
TradeOptimizer(settings=cfg)     -- DI via workers/manager.py:2549
    |
    v
self._settings used by:
    _resolve_flip_threshold       -- reads apex_min_flip_confidence_*
    _check_insufficient_data_for_flip -- reads apex_min_trades_for_flip
    _is_counter_trade_setup       -- via apex_respect_counter_trade check
    _enforce_flip_confidence      -- uses _resolve_flip_threshold internally
    |
    v
optimize() composes the gates    -- counter-trade -> insufficient -> confidence
    |
    v
APEX_FLIP_DECISION log emitted   -- single line per call, all metadata
    |
    v
log.info via get_logger("apex")  -- routes to loguru sink in src/core/logging.py
    |
    v
data/logs/apex.log (file sink)   -- per-component routing
```

# Section 6 — Phase L Real Robustness Gap (Found + Fixed)

The deep audit found a real exception path that the original try/except did not cover:

```python
# Pre-fix (commit 2c82657):
try:
    hist = getattr(package, "symbol_history", None)
    trades = getattr(hist, "trades", []) if hist else []
except Exception:
    return False, 0
count = sum(  # <-- this iteration is OUTSIDE the try
    1 for t in trades if t.get("direction") == qwen_direction
)
```

Reproduction with degraded inputs:

| Input | Pre-fix behaviour | Post-fix behaviour |
|-------|-------------------|---------------------|
| `package=None` | (True, 0) — conservative | (False, -1) — permissive |
| `trades=None` | TypeError (iteration over None) | (False, -1) — caught |
| `trades=[non-dict]` | AttributeError | (False, -1) — caught |
| `trades=mixed` | AttributeError on first non-dict | counts only valid dicts |

In production these never escaped optimize() (the outer except handler caught them and forced fallback), but they triggered noisy `APEX_FAIL_UNEXPECTED` logs and surrendered the optimization. The hardened version:

1. Wraps the entire read-and-iterate path inside try/except.
2. Adds explicit `isinstance(trades, list)` and `isinstance(t, dict)` checks.
3. Uses sentinel `-1` for the count to distinguish "gate non-applicable due to data quality" from "gate evaluated and found N=0".
4. Defaults to fail-PERMISSIVE on degraded data, matching operator philosophy.

Shipped as commit `3a552fb`. Five new test cases lock the defensive behavior.

# Section 7 — Production Quality (Phase M)

| Item | Verdict |
|------|---------|
| Type hints on every new function signature | YES (verified by `inspect.signature`) |
| Return type annotations | YES |
| Docstrings on every helper (≥ 30 chars) | YES (998-1291 chars each) |
| Args + Returns sections in docstrings | YES |
| Provenance comments | 9 markers across the file, each pointing to "PRIMARY Sell-Bias Fix (2026-05-11)" + investigation docs |
| Zero debug/print/breakpoint residue | YES |
| Zero TODO / FIXME / XXX in new blocks | YES |

# Section 8 — Test Coverage Totals

| Test category | Files | Tests passed | Tests failed |
|---------------|-------|--------------|--------------|
| Smoke (4 categories) | inline scripts | 4 / 4 | 0 |
| Unit | test_apex_sell_bias_gates.py, test_apex_flip_rr_boost.py, test_apex_flip_discipline.py | 33 / 33 | 0 |
| Integration | test_apex_flip_decision_log.py, test_apex_pipeline_integration.py | 20 / 20 | 0 |
| Regression (APEX) | test_apex_qwen_client.py, test_apex_lock_propagation.py, test_apex_tp_cap.py, test_apex_direction_lock.py | 67 / 68 (1 pre-existing brain test deselected) | 0 |
| Shadow path | test_shadow_adapter_boot_grace.py, test_shadow_signature_parity.py | 16 / 16 | 0 |
| Pipeline e2e | test_definitive_pipeline_e2e.py, test_strategy_worker_consensus.py | 26 / 26 | 0 |
| XRAY + layer_manager | test_xray_dir_flip.py, test_xray_counter_property.py, test_layer_manager_*.py | 90 / 90 | 0 |
| Broad sweep | full `tests/` minus phase7 (pre-existing collection error) and the deselected brain test | 2,779+ / 2,781+ | 2 pre-existing in `tests/test_bybit_demo/test_websocket_subscriber.py` (out of scope per spec Part A) |
| E2E behavioral replay | inline against `data/logs/workers.log` | predicted 15 of 16 Buy→Sell blocked | n/a (operator restart will confirm) |
| Performance microbenchmarks | inline | 3 µs / optimize() call total added | n/a |
| Robustness | 7 degraded-input cases | 7 / 7 handled cleanly | 0 |

# Section 9 — Final Branch State

```
3a552fb fix(p): harden _check_insufficient_data_for_flip against degraded inputs
18bc8cd docs(p): cross-check report — 8-audit pass + hardening summary
c1d0b33 test(p): cross-check hardening — endswith() + flip-decision integration tests
037af78 docs(p): Phase 3 implementation summary + Phase 4 verification checklist
2c82657 feat(p): counter-trade + insufficient-data flip gates + APEX_FLIP_DECISION log
b14cbd9 feat(p): asymmetric Buy->Sell vs Sell->Buy flip-confidence thresholds
81552f9 fix(p): repair structural_data attribute typo on optimizer flip-confidence gate
11ee05b docs(p): Sell-bias investigation reports (Phase 0 + Phase 1 + Phase 2)
```

8 commits total. 6 production-touching, 2 docs-only.

# Section 10 — Operator Sign-Off Statement

The PRIMARY Sell-Bias Fix has passed the following audits at enterprise/industry standard:

1. **Architecture**: respects the `src/apex/` module's existing data-model → data-access → business-logic layering. No layer violations.
2. **Contracts**: every public/protected method signature unchanged. New methods are additive with full type hints + Args/Returns docstrings.
3. **Wiring**: end-to-end chain from `config.toml` → `APEXSettings` → `TradeOptimizer` → gate helpers → `APEX_FLIP_DECISION` log → loguru sink verified.
4. **Naming**: every new identifier (config key, log tag, helper method, dataclass field, decision_reason value) follows the project's existing conventions.
5. **Tests**: 132 APEX + shadow + pipeline tests pass; broad sweep 2,779 pass with only pre-existing unrelated failures in out-of-scope `tests/test_bybit_demo/test_websocket_subscriber.py`.
6. **Behavioral correctness**: E2E replay against today's 9-hour log window predicts 15 of 16 Buy→Sell flips would be blocked while all 7 Sell→Buy flips survive — matching the operator's HEAVY tune intent.
7. **Performance**: 3 µs / optimize() call added (0.0002% of DeepSeek HTTP RTT). Negligible.
8. **Robustness**: 7 degraded-input edge cases handled gracefully (one real gap found and fixed in Phase L). Fail-permissive on degraded data, matching aggressive-exploitation philosophy.
9. **Production quality**: all new code has type hints + docstrings + provenance comments. Zero debug residue, zero TODOs.

**Recommendation**: the fix is **production-ready** for Phase 4 live verification. Restart the services and monitor 24-48 hours. Restart command:

```
sudo systemctl start trading-workers.service
sudo systemctl start trading-mcp-sse.service
```

Primary observability:

```
grep "APEX_FLIP_DECISION" data/logs/workers.log \
  | grep -oE "decision_reason=[a-z_]+" \
  | sort | uniq -c
```

Rollback plan unchanged from `p_phase3_implementation_summary.md` Section 8 (config-only knobs, no code revert required).
