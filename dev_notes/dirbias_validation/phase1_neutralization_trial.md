# Phase 1 — R4 + APEX Flip Threshold Neutralization Trial

Shipped 2026-05-19, restart at 13:44:48 UTC.

## Phase 1A — R4 portfolio cap config neutralization

**TOML edit** (`config.toml`, end of `[apex]` section ~line 1568):
```toml
portfolio_direction_cap_enabled = false
```

**Settings round-trip verification** (live `Settings.load()` at 13:43:30):
```
portfolio_direction_cap_enabled = False    (expect False)
portfolio_direction_cap_pct     = 0.70     (unchanged but inert)
```

**Effect**: `src/apex/gate.py:664` reads `getattr(self._settings, "portfolio_direction_cap_enabled", True)`. With `False`, the `if cap_enabled:` block at gate.py:665 short-circuits. CHECK 15 emits no log events. Cap is a no-op.

**Reversal**: delete the line from config.toml or set to `true`, then `sudo systemctl restart trading-workers trading-mcp-sse`.

## Phase 1B — APEX flip threshold symmetric realignment

**TOML edit** (`config.toml:1552-1553`):
```toml
# BEFORE: asymmetric (Buy-favoring)
apex_min_flip_confidence_buy_to_sell = 0.95
apex_min_flip_confidence_sell_to_buy = 0.70

# AFTER: symmetric (matches global floor)
apex_min_flip_confidence_buy_to_sell = 0.70
apex_min_flip_confidence_sell_to_buy = 0.70
```

**Settings round-trip verification**:
```
apex_min_flip_confidence_buy_to_sell = 0.7    (expect 0.70)
apex_min_flip_confidence_sell_to_buy = 0.7    (expect 0.70)
apex_min_flip_confidence (global)    = 0.7    (unchanged floor)
```

**Effect**: `src/apex/optimizer.py:1614-1654` (threshold resolution) returns 0.70 for both Buy→Sell and Sell→Buy flips. Previously Buy→Sell required 0.95 (25 pp harder to flip out of Buy). Per-coin DeepSeek flip confidence is now the sole differentiator, not direction.

**Reversal**: edit values back to 0.95/0.70, restart services.

## Boot sentinels post-restart (13:44:48 UTC)

| Sentinel | Time | Status |
|---|---|---|
| `XRAY_FLIP_CONFIG` | 13:44:51.649 | fired |
| `STRAT_CALL_B_REFRAMED` | 13:44:53.832 | fired |
| `STRAT_REGIME_INSTR_REFRAMED` | 13:44:53.832 | fired |
| `STATE_LABELLER_REGIME_HAIRCUT_INIT` | 13:44:54.209 | fired |

All 4 fix-series sentinels active. No CRITICAL errors post-restart (the pre-restart `WORKER_SHUTDOWN reason=atexit` events at 13:44:47 are the OLD process's clean shutdown).

## 48-72h trial metrics

Trial window: T0 = 2026-05-19 13:44:48 UTC. End = T0 + 48-72h.

| # | Metric | Pass threshold | Hard-revert trigger |
|---|---|---|---|
| M1 | Brain direction split (Buy%) | 40-70% | <20% or >80% |
| M2 | Bybit execution split (Buy%) | 40-70% | <20% or >85% |
| M3a | Buy WR over trial (n>=10) | >=40% | <30% |
| M3b | Sell WR over trial (n>=10) | >=40% | <30% |
| M4 | Trades per hour | >=80% of pre-Phase-1 rate | <50% |
| M5 | Session PnL | not worse than -50% of baseline | <-100% baseline |
| M6 | All 4 fix boot sentinels firing | every restart | any missing |
| M7 | `PORTFOLIO_CAP_HIT` event count | **0** (confirms Phase 1A working) | any new fire |
| M8 | `APEX_FLIP_DECISION floor_used` value | 0.70 for both directions | any 0.95 still firing |
| M9 | New error events (Traceback / CRITICAL / NameError) | 0 | any |
| M10 | DB cascade events (`DB_LOCK_WAIT`) | 0 | any |

Baseline metrics for comparison: pre-Phase-1 cap fires ran ~4/day (61 fires over 14d). Pre-Phase-1 flip threshold blocked Buy→Sell flips at 0.7-0.94 confidence band (now permitted). +$11.81 session PnL during the 2h 9m post-restart-prior monitoring window.

## Decision matrix at T0+48h

| Outcome | Verdict | Action |
|---|---|---|
| M1-M10 all green | **PROCEED to Phase 2** | Phase 2A removes R4 cap code; Phase 2B collapses flip threshold to single field |
| M3a or M3b drops <30% | **HARD REVERT 1B** | Restore asymmetric flip thresholds (0.95/0.70). Continue trial with only Phase 1A active |
| M7 shows any `PORTFOLIO_CAP_HIT` | **REGRESSION on Phase 1A** | Investigate — cap should be fully disabled. Likely a code path that doesn't honor `enabled=False` |
| M8 shows `floor_used=0.95` still firing | **REGRESSION on Phase 1B** | Investigate — config didn't round-trip. Re-check Settings.load |
| Trade frequency drops >25% | **INVESTIGATE** | Cap was supposed to block trades not enable them. Unexpected drop = different cause |
| New error events | **REVERT BOTH + INVESTIGATE** | Don't proceed to Phase 2 until errors resolved |

## Phase 2 readiness checklist (deferred, after Phase 1 trial)

- [ ] All 10 metrics green for 48-72h
- [ ] Operator approval for Phase 2A code removal
- [ ] New branch `fix/remove-r4-portfolio-cap` off `main`
- [ ] Phase 2A grep-confirms zero remaining `portfolio_direction_cap` references
- [ ] Phase 2B grep-confirms zero remaining `apex_min_flip_confidence_buy_to_sell` / `_sell_to_buy` references
- [ ] All affected tests updated or removed in lockstep with code

## Operator actions during the trial

1. Watch realtime monitor for any `PORTFOLIO_CAP_HIT` events (should be zero).
2. Watch direction split — if it skews >70% in one direction with no protection, this is the operator's chance to evaluate whether the cap was load-bearing.
3. Re-enable Layer 2/3 if any operator emergency_close has them off (Layer 1 produces no trades).
4. At T0+48h, run decision matrix against captured metrics.
