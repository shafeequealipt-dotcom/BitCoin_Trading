# Phase 6 — ScannerWorker Conversion to Cycle Trigger

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 5b commit:** `252c9c6` (preceded this).

## Summary

Replaced `MarketScanner.scan_market()` raw-ticker scoring with a composite-opportunity score read from the 7 data workers' warm caches. ScannerWorker is now a separate cycle trigger (sweet spot 4:00) that selects the cycle's 30-coin focus from the 50-coin watch_list.

## Files modified

- `src/config/settings.py`:
  - New `ScannerScoringWeights` dataclass with 5 weights (structure, strategy, signal, regime, funding) defaulting to 0.30 / 0.30 / 0.15 / 0.15 / 0.10.
  - `ScannerSettings` gains `scoring_weights: ScannerScoringWeights = field(default_factory=...)`.
  - New `_build_scanner_scoring_weights()` builder.
  - `_build_scanner` extended.
- `config.toml`:
  - New `[scanner.scoring_weights]` section with the 5 weights.
- `src/workers/scanner_worker.py` (rewritten):
  - Parent `BaseWorker` → `SweetSpotWorker`. Sweet spot 4:00.
  - New constructor signature `(settings, db, scanner, services=None)`.
  - Defensive accessor lookups for `structure_worker.get_setup_score`, `strategy_worker.get_score`, `signal_worker.get_signal`, `regime_worker.get_regime`, `altdata_worker.get_funding`. Each returns None if the service is missing — no crash on partial wiring.
  - `_compute_opportunity_score(coin)` produces composite float 0..1 + per-component breakdown dict.
  - Each component normalized to 0..1 before weighting:
    - structure: setup_score / 100
    - strategy: total_score / 100
    - signal: confidence (already 0..1)
    - regime: alignment_factor mapped (-1..+1) → (0..1)
    - funding: |rate| / 0.001 (saturates at 0.1%)
  - Open-position symbols force-included via position_service (HR-3 preserved).
  - Top-N selected by `settings.scanner.max_coins` (default 30).
  - Active_universe table refreshed (DELETE + INSERT). Schema unchanged; columns no longer fed by raw ticker (volume_24h, change_24h_pct, funding_rate, spread_pct) get 0.0 placeholders since the new path doesn't fetch tickers.
  - `MarketScanner._active_universe` updated so `await scanner.get_active_universe()` returns the new list. BTC/ETH always force-included as reference pairs (preserved from legacy).
  - `MarketScanner._subscribers` notified — kept for one phase; Phase 7 trims worker subscribers.
  - New `SCANNER_TICK_SUMMARY | watch_list=50 protected=N scored=K selected=L top_n=30 forced_in=M mean_score=... top=COIN(score) el=Xms drift_ms=D | {ctx()}`.
  - New per-coin DEBUG `SCANNER_SELECTED | rank=R coin=C score=S src=structure:a,strategy:b,signal:c,regime:d,funding:e`.
- `src/workers/manager.py`:
  - ScannerWorker construction now passes `services=self._services` (by reference; later worker registrations propagate).
  - `signal_worker`, `altdata_worker`, `regime_worker`, `scanner_worker` all registered into `self._services` (in addition to existing `kline_worker`, `price_worker`, `structure_worker`, `strategy_worker`, `scanner`, `regime_detector`).

## Behavior change

- ScannerWorker's scoring path no longer hits Bybit REST (`get_all_linear_tickers`).
- Composite score combines 5 worker outputs instead of 7 raw market metrics.
- Sweet spot 4:00 places the scan AFTER all 7 data workers have completed their sweet spots, so caches are warm.
- Force-include for open positions preserved (HR-3).
- BTC/ETH reference-pair force-include preserved.

## Verification

- `Settings._load_fresh()` parses the new `[scanner.scoring_weights]` section.
- `ScannerWorker.__bases__ = (SweetSpotWorker,)`; sweet spot = 4:00; weights = 0.30/0.30/0.15/0.15/0.10.
- Live verification (drift, scoring path doesn't hit Bybit, top_n=30 selected, position force-include) deferred to Phase 9.

## Hard rule check

- HR-1: workers operate on watch_list (verified Phase 2-5).
- HR-2: ScannerWorker doesn't synchronize with workers — reads what's in cache.
- HR-3: open-position coins force-included.
- HR-4: chain order kline 0:30 < structure 0:45 < signal 1:00 < regime 1:15 < strategy 1:30 < scanner 4:00.
- HR-5: watch_list is the only source.
- HR-6: one commit.

## Risks & deferred items

- The active_universe table's volume_24h/change_24h_pct/funding_rate/spread_pct columns receive 0.0 placeholders. If anything reads those expecting live values, that's a regression. From Phase 0 investigation, only the strategist reads active_universe via `scanner.get_active_universe()` (just the symbol list); no consumer reads the auxiliary columns. Safe to leave at 0.0.
- Initial scan at boot (manager.py:532 `scanner.scan_market()`) still runs and uses the LEGACY ticker-fetch path of `MarketScanner` to seed `_active_universe` so the system isn't empty before ScannerWorker's first sweet-spot fire (4 min after boot worst case). This preserves existing behavior for cold-start. After the first sweet-spot fire, the new path takes over.
- Phase 7 will trim the rotation handlers + scanner subscriber notifications.
