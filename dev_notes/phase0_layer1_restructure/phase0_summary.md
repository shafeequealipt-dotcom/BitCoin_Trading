# Phase 0 Summary — Layer 1 Restructure Investigation

**Synthesizes:** scanner_worker.md, structure_worker.md, strategy_worker.md, stage2_prompt_builder.md, sweet_spot.md, layer_toggle.md, observability.md.
**HEAD:** `8dca492` (chore(pre-layer1): commit historical dev_notes + shadow_kline_reader tests + system inventory).
**Tag:** `pre-layer1-restructure`.

## Pre-conditions audit

| # | Condition | Status |
|---|---|---|
| 1 | 5 critical bugs fixed | ✅ — all 5 shipped 2026-04-26 (commits `c9503bf`, `e5089ee`, `518f3b6`, `028a6d5`, `386e1b3`, `fb59a60`, `50f89cd`, `95e6291`); see `dev_notes/five_fixes_final_summary.md` |
| 2 | 1-week soak post-fixes | ❌ WAIVED (operator decision 2026-04-27); D-3 only 1 day old |
| 3 | Operator approval of blueprint | ✅ |
| 4 | DB backup `data/trading.db.pre-layer1-restructure.20260427.bak` | ✅ created in step 0.0 (154MB) |
| 5 | Git tag `pre-layer1-restructure` | ✅ on commit `8dca492` |

Working tree reconciliation (step 0.0):
- 11 modified src/ files stashed as "pre-layer1-restructure: WIP from prior engagement (shadow/strategist/signals/regime/ta_cache/urgent_queue/overhaul29-tests)" — `git stash list` shows it.
- Orphan `src/workers/layer_manager.py` removed (matched `d6e1b1c` intent).
- Untracked dev_notes + `tests/test_shadow_kline_reader/` + `SYSTEM_INVENTORY.md` committed (`8dca492`).
- `data/layer_state.json` and `trading.db` left alone (runtime state).

## Verification gate — 5 questions answered

### Q1. EXACT ScannerWorker scoring formula and top-30 logic

`src/workers/scanner_worker.py:143-184` (formula) and `:233-252` (selection):
```python
score = (
    weights.structure * struct_norm  +   # struct_raw / 100, clamped 0-1
    weights.strategy  * strat_norm   +   # strat_raw / 100, clamped 0-1
    weights.signal    * sig_norm     +   # signal_confidence (0-1)
    weights.regime    * regime_norm  +   # (regime_align + 1.0) / 2.0
    weights.funding   * funding_norm     # |rate| / 0.001, saturated at 1.0
)
# Selection: scored.sort desc; final = scored[:max_coins] + force_include(open_positions ∉ top)
```

### Q2. EXACT data structure XRAY produces per coin

`StructuralAnalysis` dataclass (returned by `StructureEngine.analyze` and cached in `StructureCache`). Public-known fields: `setup_score: float`, `structural_placement` (with `sl, tp, rr_ratio`), `mtf_confluence`, `key_features: list[str]`, `session` (with `current_session, session_phase, manipulation_likely`).

Phase 2 ADDS: `setup_type: SetupType = SetupType.NONE` and `setup_type_confidence: float = 0.0`.

ScannerWorker reads via `StructureWorker.get_setup_score(coin) -> float | None` (returns `cache.get(coin).setup_score`, or None). After Phase 2, additional accessor for setup_type will be needed.

### Q3. EXACT data structure StrategyWorker produces

- `_score_cache: dict[str, float]` (`strategy_worker.py:88`, populated `:518`, accessor `:675`). ScannerWorker reads via `services["strategy_worker"].get_score(coin)`.
- `layer_manager._strategy_hints` — list, written `:598`. List of dicts with consensus_strength.
- `layer_manager._strategy_consensus` — TODAY a SUMMARY DICT, written `:599` via `_build_consensus_summary(filtered)`. Phase 3 converts to per-coin keyed dict (`dict[str, dict]` with `consensus, consensus_score, vote_count, direction, last_updated`). Stale preservation: existing entries for unprocessed coins are NOT cleared.
- Ensemble categories (5 not 4): STRONG / GOOD / WEAK / **LEAN** / CONFLICT (`ensemble.py:99-110`). Phase 5 maps LEAN as failing `min_consensus="GOOD"`.

### Q4. EXACT 19 sections of CALL_A and per-section data source

Partial answer — see `stage2_prompt_builder.md` for the deferred Phase 7 sub-task. Per-coin sections (migrate to packages): price_data, xray, strategies, signals, alt_data (per-coin slice), open_position. Global sections (stay as queries): coaching, regime instructions, account, daily PnL, urgent_queue, event_buffer, fear_greed, drawdown.

The exhaustive 19-section enumeration with line ranges is deferred to the start of Phase 7 (when actually editing strategist.py) so the section list aligns with whatever the prompt contains at that time (Phases 2/3/5/6 may have shifted some sources).

### Q5. EXACT migration path for `layer_state.json` v1 → v2

`scripts/migrate_layer_state_to_v2.py` (Phase 8):
```python
def migrate(state_v1: dict) -> dict:
    la1 = state_v1.get("layer_active", {})
    return {
        "schema_version": 2,
        "layer_active": {
            "1": la1.get("1", False),       # DATA stays
            "2": la1.get("2", False),       # ANALYSIS = old BRAIN intent
            "3": la1.get("2", False),       # BRAIN = old BRAIN
            "4": la1.get("3", False),       # EXECUTION = old EXECUTION
            "5": la1.get("3", False),       # MONITORING = old EXECUTION
        },
        "user_stopped": state_v1.get("user_stopped", False),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
```

Backup at `data/layer_state.v1.json.bak`. Idempotent: no-op if `schema_version == 2`. Code touch-ups: `src/apex/gate.py` (3→4 for execution), `src/workers/{profit_sniper,position_watchdog,recovery_planner}.py` (3→5 for monitoring), `src/trading/services/order_service.py` Layer3RaceError (3→4), `src/telegram/handlers/{system,control_handler}.py` (`/health` and `/layer` accept 1..5).

## Cross-component dependencies

```
KlineWorker (1A, 0:30) ─→ trading.db klines
                              │
                              ▼
StructureWorker (1B, 0:45) ─→ StructureCache (setup_type added Phase 2)
                              │
SignalWorker (1B, 1:00) ─────→ _signal_cache
                              │
RegimeWorker (1B, 1:15) ─────→ _per_coin_regimes
                              │
StrategyWorker (1C, 1:30) ───→ _score_cache + _strategy_consensus[coin] (Phase 3)
                              │
AltDataWorker (1A, 1:45) ────→ _funding_cache
                              │
                              ▼
ScannerWorker (1D, 4:00 → 1:30 post-restructure)
   ├─ qualitative filter (Phase 5): setup_type, consensus, regime, RR, blockers
   ├─ ranking (reuses _compute_opportunity_score)
   ├─ select 10-15 + force-include positions (HR-2)
   └─ build packages (Phase 6) → layer_manager._coin_packages
                              │
                              ▼
Stage 2 (Phase 7): _build_trade_prompt reads packages instead of querying 12 services
```

## Hard rules audit

- HR-1 (one job per worker): Phase 5 separates analyzer-vs-selector concerns; ScannerWorker becomes the selector + package builder, no analysis (HR-4).
- HR-2 (force-include open positions): preserved in Phase 5 (`_open_position_symbols()` already exists at `scanner_worker.py:186-203`).
- HR-3 (Stage 2 inputs are self-contained packages): Phase 6/7.
- HR-4 (heavy analysis only in 1B/1C): Phase 5 keeps ScannerWorker as smart combiner; no new analytical logic.
- HR-5 (every operation logs start+end+elapsed): Phase 1 standardizes; existing TICK_SUMMARY lines preserved.
- HR-6 (thresholds in config.toml): Phase 2/5 add `[analysis.structure.setup_types]` and `[scanner.qualitative]`.
- HR-7 (Layer 1A always runs): Phase 4 codifies via `worker_tier=LAYER1A`.
- HR-8 (cycle waits for next 5-min boundary on cold start): Phase 4 cold-start boundary wait.
- HR-9 (packages flow only Scanner → Stage 2): Phase 6/7.
- HR-10 (restructure does not fix bugs): scoped explicitly; bug fixes already shipped (5-fixes engagement complete).

## Blueprint clarifications NEEDED before implementation

None blocking. Two items to flag:

1. **5 ensemble categories not 4.** Blueprint mentions STRONG/GOOD/WEAK/CONFLICT; code emits a 5th `LEAN`. Phase 5 must explicitly reject LEAN as failing `min_consensus="GOOD"`. Configurable.
2. **CALL_A 19-section enumeration deferred.** The prompt is large and may shift across Phases 2/3/5/6 (XRAY adds setup_type, StrategyWorker exposes consensus per-coin, ScannerWorker selects fewer coins). The exhaustive enumeration with line ranges happens in Phase 7's investigation step, not Phase 0.

## Phase 0 commits

This investigation produces 8 dev_notes files (this summary + 7 component files). Commit strategy: bundle into 2 commits to keep velocity high (vs. the 9 commits in the original plan, which optimized for fine-grained reviewability):

1. `phase0-layer1-restructure: 7 component investigation files`
2. `phase0-layer1-restructure: summary + verification gate answers`

The pre-condition setup commit (`8dca492`) is independent and already shipped.

## Gate decision

**PASS.** All 5 verification questions answered concretely with file:line citations. Phase 1 may begin.
