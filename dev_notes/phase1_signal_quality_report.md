# Phase 1 — SignalWorker NEUTRAL Distribution Fix Report

**Date:** 2026-04-27
**Commits:**
- `07b4901` — soften sentiment hard gate — multi-source direction scoring (source + config)
- `a724113` — unit tests for multi-source signal classification

## Bug summary

`SignalGenerator._evaluate_signal()` at `src/intelligence/signals/signal_generator.py:313-375` (pre-fix) used sentiment as a HARD gate. Every BUY/SELL classification rule required `abs(sentiment) > 0.2`. With sentiment=0.0 in 97.9% of coins (Reddit disabled by config + Finnhub free tier no altcoin coverage + `aggregator.py:163-165` zero-coverage rule, all intentional Phase 15 design), all signals fell through to NEUTRAL by design.

**Live measurement (5h log window, 65 batches × 50 coins = 3,224 signals):**
- 100% NEUTRAL (3,224/3,224)
- avg confidence 0.290, std 0.05
- 25 / 3,224 (0.77%) had confidence ≥ 0.40
- ZERO `SIG_DOWNGRADE` events (Phase 29 gate unreachable — confirms the gate was NOT the root cause despite the prompt's assumption)

## Fix summary

Replaced the 9-rule cascade with a multi-source weighted classifier. Four components participate independently:

| Component | Score formula | Direction convention |
|---|---|---|
| sentiment | `clamp(sentiment, -1, 1)` | + = bullish |
| F&G (contrarian) | `(50 - fear_greed) / fg_normalize_range` | F&G low → +1 (bullish) |
| funding (inverted) | `-funding_rate / funding_normalize` | high positive funding → -1 (bearish — crowded longs) |
| OI change | `oi_change / oi_normalize_pct` | trend-following |

Each component is **active** only if `abs(score) >= component_min_active`. INACTIVE components are DROPPED from the weighted sum (they do NOT pull toward NEUTRAL by occupying weight). Weights renormalised over the active set. Final mapping: `>= +strong` → STRONG_BUY, `>= +buy` → BUY, etc.

The post-fix property guaranteed by the new classifier: **a coin with `sentiment=0.0` (no qualitative data) can still classify BUY/SELL via F&G + funding + OI alone** — the load-bearing test `test_zero_sentiment_can_classify_buy_via_fg_and_funding` enforces this.

## Files changed

| File | Change |
|---|---|
| `src/intelligence/signals/signal_generator.py` | `__init__` accepts optional `settings` (back-compat); `_evaluate_signal` rewritten as multi-source classifier; `generate_signal` emits `SIG_GEN_INPUT` before classification; `SIG_CLASSIFY` emitted inside the classifier |
| `src/config/settings.py` | New `SignalGeneratorMultiSourceSettings` (13 tunables, validated); `SignalGeneratorSettings` wrapper; `Settings.signal_generator` field; `_build_signal_generator` parser |
| `src/workers/manager.py` | `SignalGenerator(aggregator, db, settings=settings)` |
| `src/mcp/server.py` | Same |
| `config.toml` | `[signal_generator.multi_source]` block with operator-facing comment |
| `tests/test_signal_generator_multi_source.py` (NEW) | 13 cases — load-bearing post-fix property + 12 boundary/integration cases |

## New observability

| Tag | Level | Frequency | Purpose |
|---|---|---|---|
| `SIG_GEN_INPUT` | INFO | one per coin per cycle | Which inputs were active before classification |
| `SIG_CLASSIFY` | INFO | one per coin per cycle | Component scores + direction_score + final type |

Existing `SIG_GEN`, `SIG_BATCH`, `SIG_TICK_SUMMARY`, `SIG_BATCH_STATS` all preserved (back-compat).

## Verification — automated

```
pytest tests/test_signal_generator_multi_source.py
       tests/test_layer_state_sync.py
       tests/test_layer_manager_persistence.py
       tests/test_worker_liveness.py
       tests/test_worker_liveness_watchdog.py
       tests/test_logging_routing.py
       tests/test_corrected_layer1_integration.py
       tests/test_universe_settings.py
99 passed in 1.95s
```

End-to-end Settings round-trip:
```
signal_generator.multi_source.fg_weight: 0.25
SignalGenerator constructed with settings OK; ms_cfg is settings instance: True
```

## Verification — operator-driven (post-deploy, ~2 hours)

| # | Trial | Pass criterion |
|---|---|---|
| 1.1 | Distribution check across 24 cycles × 50 coins | BUY 15-30%, SELL 15-30%, NEUTRAL 40-70%. ALL NEUTRAL = fail. ALL one-side = fail. |
| 1.2 | Confidence histogram (`SIG_BATCH_STATS`) | mean 0.4-0.6, std > 0.10 |
| 1.3 | 10 strongly-trending coins (visually verified) — directional match | ≥70% match the trend direction |
| 1.4 | ScannerWorker qualified count rise | average 5-25 per cycle (was 0-2) |
| 1.5 | `SIG_CLASSIFY` per signal | one event per signal per cycle, structured fields |
| 1.6 | Phase 29 gate now reachable (`SIG_DOWNGRADE` logs) | ≥1 event in 1 hour confirms gate fires when needed |

## Rollback strategy

- **Per-commit revert:** `git revert a724113 07b4901` removes both the tests and the source change.
- **Config-only emergency softening:** set `[signal_generator.multi_source] strong_threshold = 0.99` and `buy_threshold = 0.99` to suppress all BUY/SELL classifications without redeploy. Operator can do this via SSH if the new logic produces too many false positives in the field.
- **Per-component disable:** set any component's `<component>_min_active` to a value greater than 1.0 to disable that component entirely.

## Out of scope for this phase

- SentimentAggregator zero-coverage rule (Phase 15 design — UNCHANGED)
- Strategy categorisation (out of scope)
- ScannerWorker selection criteria (Phase 5+ work)
- Phase 7 sentiment categorical tag refinement (separate phase)
