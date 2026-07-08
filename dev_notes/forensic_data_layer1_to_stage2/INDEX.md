# Forensic Data Collection — Layer 1 → Stage 2 Pipeline

**Capture window:** 2026-04-27 22:53 UTC – 23:20 UTC (workers PID 399, started 22:53:35 UTC)
**DB snapshot:** `_trading_db_snapshot.db` (147 MB, captured 22:56:15 UTC)
**Live process:** verified running throughout collection (heartbeat `total=19 healthy=14→19 cycle_active=True`)

This directory contains 25 forensic files produced under the strict rules of `COLLECT_LAYER1_TO_STAGE2_PIPELINE_FORENSIC_DATA.md` (verbatim, measured, evidence-based, gap-honest, no fix proposals).

A single consolidated copy of all 25 files is also available at `COMPLETE_FORENSIC_COLLECTION.md`.

## Module A — Data Sources
- [A1_external_apis.md](A1_external_apis.md) — Bybit REST/WS, Finnhub, Alternative.me F&G, CoinGecko inventory + 9 documented gaps

## Module B — Layer 1A Workers
- [B1_price_worker.md](B1_price_worker.md) — `_ws_quotes` + `ticker_cache` wiring; ~5,500 msgs/min, 50 coins
- [B2_kline_worker.md](B2_kline_worker.md) — Multi-TF schedule, executemany write pattern; `KLINE_WRITE_DONE` tag NOT FOUND
- [B3_altdata_worker.md](B3_altdata_worker.md) — funding/OI/F&G/onchain cadences; F&G skipped 11/12 wakeups by design
- [B4_news_worker.md](B4_news_worker.md) — `SYMBOL_EXTRACTION_MAP` only covers 10 of 50 watch_list base assets
- [B5_sentiment_aggregator.md](B5_sentiment_aggregator.md) — SENT_UNKNOWN root cause: Reddit disabled + 44/50 coins untagged

## Module C — Layer 1B Analyzers
- [C1_structure_worker.md](C1_structure_worker.md) — 12 X-RAY phases, batch_size=25 (not 2 as prompt stated), live setup_type distribution
- [C2_signal_worker.md](C2_signal_worker.md) — Phase-29 gate is NOT cause of NEUTRAL; upstream classifier is. BTCUSDT trace pinned.
- [C3_regime_worker.md](C3_regime_worker.md) — APEX/TIAS use SAME accessor (no key mismatch); 8/49 first-tick changes bypass hysteresis
- [C4_ta_cache.md](C4_ta_cache.md) — TTL=120s (not 90s default), maxsize=200, 9 production callers

## Module D — Strategy Pipeline
- [D1_strategy_worker.md](D1_strategy_worker.md) — 4 internal layers, 30 of 39 strategies fired 0×, B4=73% of L1 signals
- [D2_strategy_performance.md](D2_strategy_performance.md) — Only `claude_trader` populates table; A1-K4 absent. `strategy_trades` closure fields never UPDATEd

## Module E — Smart Scanner
- [E1_scanner_worker.md](E1_scanner_worker.md) — `qualified=0` decomposition: 25+10+12+2+1=50 with verbatim XRAY_NONE_REASON samples
- [E2_coin_package_builder.md](E2_coin_package_builder.md) — Cold-start 0.67 root-cause per missing field; `_enrich_for` AttributeError fall-through

## Module F — Cross-Layer Wiring
- [F1_inter_worker_caches.md](F1_inter_worker_caches.md) — 13 caches inventoried; NO runtime introspection mechanism
- [F2_db_tables.md](F2_db_tables.md) — 15 tables; D-3 lock peak 137,403 ms on 2026-04-26 21:14:54
- [F3_sweet_spot_scheduling.md](F3_sweet_spot_scheduling.md) — Per-worker schedule + cycle gate verbatim; live drift 0–28 ms
- [F4_data_flow_trace.md](F4_data_flow_trace.md) — End-to-end cycle 22:25:00 timing; package age 163–287s at Brain CALL_A

## Module G — Configuration & Live State
- [G1_config_toml.md](G1_config_toml.md) — Verbatim 1094-line config.toml, md5 d5c308beb5441fb193217013e3f3a545
- [G2_hardcoded_thresholds.md](G2_hardcoded_thresholds.md) — 27 threshold groups not in config; 4 sources of truth for `min_rr_ratio`
- [G3_live_cache_snapshots.md](G3_live_cache_snapshots.md) — Reconstructed from logs; no in-process dump endpoint
- [G4_scanner_cycles.md](G4_scanner_cycles.md) — Last 7 cycles ALL `qualified=0 forced=2`
- [G5_brain_cycles.md](G5_brain_cycles.md) — Last 5 CALL_A: 0/2/2/2/2 packages, 2/2/1/0/2 trades; placement results not in logs
- [G6_errors_24h.md](G6_errors_24h.md) — 79 ERROR/CRITICAL events, ORDER_GATE_LM_DEADLINE_EXCEEDED=20, ORDER_BLOCKED=20
- [G7_worker_inventory.md](G7_worker_inventory.md) — 19 registered workers; 7 "dormant" workers confirmed gated/removed

## Verification Gate
1. ✅ Every file exists and is non-empty (43,713 → 9,352 bytes)
2. ✅ Code references cite file:line in source repo
3. ✅ Live measurements have actual timestamps (22:05–23:20 UTC window)
4. ✅ Cache snapshots either contain values or document "NOT FOUND — reconstructed from..."
5. ✅ config.toml pasted verbatim with md5 fingerprint
6. ✅ Hardcoded thresholds enumerated against config

## Pre-Condition Notes (full transparency)
- Available log window: ~1h20m current `workers.log` (22:10–22:59) plus rotated `workers.2026-04-27_01-31-00`. No continuous 24h trail; "last 24h" counts bounded by visible window.
- Workers process restarted at 22:45:52→22:53:26 (clean atexit) mid-collection — `fires` counter reset noted in F3.
- `KLINE_WRITE_DONE`, `SCANNER_QUALIFY` per-coin emissions, runtime cache-dump endpoint: NOT FOUND (documented per Hard Rule 5, not fabricated).

## What This Document Is NOT
This is forensic data ONLY. No fix proposals. No architecture critiques. The external designer reads these 25 files (or `COMPLETE_FORENSIC_COLLECTION.md`) and writes a precise fix plan separately.
