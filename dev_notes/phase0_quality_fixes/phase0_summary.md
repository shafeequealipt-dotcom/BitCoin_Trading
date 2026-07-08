# Phase 0 — Cross-Issue Summary

## Investigation status

7 quality issues investigated, all 4 verification-gate questions answered concretely for each:

| # | Issue | WHERE root cause | WHAT current state | EXPECTED | HOW to verify |
|---|---|---|---|---|---|
| 1 | SignalWorker NEUTRAL | `signal_generator.py:313-375` (sentiment hard gate) + `aggregator.py:163-165` (zero-coverage rule, by-design) | 100% NEUTRAL, conf 0.29 | BUY/SELL/NEUTRAL spread, conf 0.4-0.6 | post-fix `SIG_TICK_SUMMARY` distribution |
| 2 | XRAY setup_type | `structure_worker` (no `XRAY_CLASSIFY_SUMMARY` emit) — logic at `structure_engine.py:676-803` is correct | unknown distribution (no log) | NONE 30-60%, BULLISH+BEARISH ≥30% | `XRAY_CLASSIFY_SUMMARY` per cycle |
| 3 | RegimeWorker per-coin | `regime.py:117-145` (hardcoded thresholds), `regime_worker.py:200-211` (no per-coin emit) | global=trending_down, per-coin invisible | mixed per-coin, ≥3 categories | `REGIME_PERCOIN_SUMMARY` |
| 4 | Stage 1 L1-L4 internals | `strategy_worker.py` L1-L4 emits — count + elapsed only, no distribution | working but invisible | per-strategy fire rates / score percentiles / consensus dist | extended emits with new fields |
| 5 | CoinPackage validation | `scanner_worker.py:300-483` (no validation) | packages with NULL/default fields can flow to Stage 2 | 90%+ ok, ≤5% warn, rare fail | `PACKAGE_VALIDATE_SUMMARY` |
| 6 | Cross-cycle freshness | NO instrumentation today | invisible | end-to-end p50 ~90s | `CYCLE_FRESHNESS` per cycle |
| 7 | Sentiment categorical | `aggregator.py:192-198` (single SENT_UNKNOWN tag) | 6190 SENT_UNKNOWN_CACHE_HIT — operators can't differentiate causes | 4 categorical tags | grep distribution shows most events `SENT_DEGRADED_MODE` |

## Cross-issue dependencies

| Issue X | Affects Issue Y? | How |
|---|---|---|
| 1 | 4, 5, 6 | If Q1 fix produces real signals, Stage 1 L2-L4 distributions change; CoinPackage validation will see populated `signals.confidence`; freshness measurement unaffected (instrumentation, not output) |
| 2 | 3, 4, 5 | If Q2 surfaces XRAY classification distribution, ScannerWorker criterion 1 pass rate becomes measurable; downstream pipeline affected |
| 3 | 4, 5 | If per-coin regime variety emerges, ScannerWorker criterion 3 has data; CoinPackage `price_data.regime` more meaningful |
| 4 | 5 | Stage 1 internal observability does not change Stage 1 outputs; CoinPackage `strategies.*` block unchanged |
| 5 | nothing | Quarantine logic is purely defensive — drops bad packages before Stage 2 |
| 6 | nothing | Pure observability; no behaviour change |
| 7 | nothing | Pure observability; no behaviour change |

## Phase order — confirmed correct, no changes needed

The prompt's order (1 → 7) reflects increasing observability vs decreasing user impact:
- 1, 2, 3 are user-visible quality fixes (signal direction, XRAY variety, regime variety)
- 4 is internal observability for Stage 1
- 5 is the contract gate with Stage 2
- 6 is pipeline-level observability
- 7 is diagnostic clarity

No reordering needed.

## Pre-conditions for Module 1 execution

The dead-workers fix (commits `7b2faaa..3541cba`, on disk, undeployed) must be in production before:
- Phases 2, 3 (live verification trials need cycle-gated workers actually ticking — currently they skip per L3=OFF)
- Phase 4 (Stage 1 distributions only meaningful if strategy_worker is ticking)
- Phase 5 (CoinPackage validator only meaningful when packages are being built)
- Phase 6 (cross-cycle freshness only measurable when all Layer 1 workers tick)
- Phase 7 (sentiment categorical reasons measurable in any state — pre-existing aggregator runs)

Phases 1 and 7 can ship + verify against the current pre-fix state. The rest need restart.

## Operator deployment sequence (per user Q1 = "run together later")

```
1. cp data/trading.db data/trading.db.bak-pre-output-quality-fix-$(date +%Y%m%d-%H%M%S)   # ✓ DONE in pre-Phase-0
2. git tag pre-output-quality-fix                                                          # ✓ DONE
3. pm2 restart workers   # deploys dead-workers fix (commits 7b2faaa..3541cba)
4. Wait ~30 min — confirm WORKER_LIVENESS_HEARTBEAT every 30s, no NEVER_TICKED
5. /start trading via Telegram — confirm L3 stays ON (no auto-revert)
6. Wait ~30 min — observe healthy state
7. Begin Phase 1 implementation (3 commits)
8. Restart, verify 1 hour
9. Sequentially: Phase 2 → 3 → 4 → 5 → 6 → 7, each with restart + 1h verification
```

## Risk register

| Risk | Mitigation |
|---|---|
| Q1 multi-source fix produces too many BUY/SELL (false positives) | Threshold tuning via `config.toml [signal_generator.multi_source]` post-deploy; rollback config flag |
| Q2 reveals XRAY classification IS heavily skewed → threshold calibration needed | Add Phase 2.3 (calibration) only if log evidence shows mis-calibration; not pre-committed |
| Q3 config exposure changes regime defaults from current hardcoded → behaviour change | Set defaults to match current hardcoded values exactly; operators see no change unless they tune |
| Q5 validator threshold too strict → most packages quarantined | `fail_below=0.5` calibrated against typical observed completeness 0.85-1.0; threshold via config |
| Q6 cache_freshness adds per-tick overhead | Module-level singleton dict, < 1 ms; if any worker shows >5% latency rise, that hook reverts only |
| Q7 categorical tags break downstream parsers | Keep `SENT_UNKNOWN` as alias; downstream parsers continue to work |

## Conclusion

Investigation complete. All 7 issues have concrete root cause + verification approach. No phase reordering needed. Module 1 ready to execute sequentially after the dead-workers fix is deployed.
