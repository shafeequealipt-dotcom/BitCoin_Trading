# CRITICAL-5 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

CRITICAL-5 — Stop-loss placed on wrong side of price for Sell positions.

## Phase 0 baselines

- 5x KATUSDT BYBIT_DEMO_SET_SL_FAIL between 14:33:43 and 14:34:12 (28s burst)
- 2x RENDERUSDT BYBIT_DEMO_SET_SL_FAIL at 16:09 (with HIGH-9 cross-symbol tid bleed: tid=t-ATOMUSDT-sniper)
- 1x ICPUSDT BYBIT_DEMO_SET_SL_FAIL with ret_code=34040 "not modified" (different bug — idempotent retry)

## Investigation — single-stepping the KATUSDT failure

Trace from logs:

| Time | Event |
|---|---|
| 14:32:33.082 | M4_TRAIL sym=KATUSDT new_sl=0.01021440 old_sl=0.01024000 dist=0.00005246 (0.52%) peak=0.01012500 dir=Sell |
| 14:33:10.832 | SL_GATEWAY_ACCEPT old=0.010210 new=0.010184 src=profit_sniper_trail dist_pct=0.290 |
| 14:33:10.833 | M4_TRAIL new_sl=0.01018448 old_sl=0.01021000 peak=0.01012500 |
| 14:33:43.787 | M4_DECISION action=tighten score_action=partial_close score=59.9 |
| **14:33:43.860** | **BYBIT_DEMO_SET_SL_FAIL sym=KATUSDT sl=0.01015569 err=...StopLoss:1015000 set for Sell position should greater base_price:1017000** |
| 14:33:43.860 | SL_GATEWAY_WIRE_FAIL new=0.010156 src=profit_sniper_trail rsn=service_returned_false |

Root cause confirmed:

1. `profit_sniper._compute_trail_stop` (lines 1267-1273) computes `trail_stop = peak_price + trail_distance` for Sell. Capped only against entry_price (breakeven floor at line 1273).
2. `peak_price` is the LOWEST price reached during the position (most profit on Sell).
3. As current price retraces UP (Sell going against you), the trail SL stays anchored to peak. Eventually `trail_stop < current_price`, putting SL on the wrong side.
4. KATUSDT case: peak=~0.01010, trail_stop=0.01015569, current_price=0.01017 (0.014% above SL). Bybit rejects with ret_code 10001.
5. The `_apply_trail_stop` function at line 1505 has a SNIPER_TOO_CLOSE check that uses `abs(symbol_price - new_sl_candidate)` — direction-agnostic. Wrong-side SL with absolute distance > min passes the check.
6. The `sl_gateway.apply` R2 check at `sl_gateway.py:414` also uses `abs(current_price - new_sl)` — direction-agnostic. Wrong-side SL passes.
7. Bybit's matching engine catches the wrong-side SL but only after a network roundtrip, generating BYBIT_DEMO_SET_SL_FAIL alerts (CRITICAL severity).

Each of the 5 KATUSDT failures was a separate sniper tick re-attempting the same wrong-side SL.

## Why the audit's tid was correct for KATUSDT but wrong for RENDERUSDT

KATUSDT: tid=t-KATUSDT-sniper (correct attribution)
RENDERUSDT: tid=t-ATOMUSDT-sniper (cross-symbol bleed — HIGH-9). The bleed indicates the sniper iterates symbols within a single tick without resetting ctx between iterations. This is a separate bug; the sniper IS still the source for both KATUSDT and RENDERUSDT failures.

## ICPUSDT — different bug class

```
BYBIT_DEMO_SET_SL_FAIL | sym=ICPUSDT sl=3.5275905 ... ret_code=34040 not modified
```

Bybit returned 34040 ("not modified") because the caller asked to set SL=3.5275905 when the existing SL is already 3.5275905. This is an idempotent retry — should be treated as success, not failure. The current adapter at `bybit_demo_adapter.py:535-541` lumps all TradingMCPError as failure. Same idempotent-handling pattern exists at `set_leverage:519` for ret_code 110043 — mirror it.

## Three options considered

### Option A — Fix sniper only (root cause)

Add wrong-side guard in `_apply_trail_stop` (after SNIPER_TOO_CLOSE check). If `trail_stop` is on wrong side of current_price, log SNIPER_WRONG_SIDE and return False without calling gateway.

Pros: minimal blast radius (one file).
Cons: doesn't catch other callers (watchdog, time-decay, CALL_B); doesn't catch ICPUSDT 34040 case; alerts still fire if sniper bug regresses.

### Option B — Fix adapter only (defensive)

Add validate-and-reject in adapter's `set_stop_loss` and `set_take_profit`. Read pos.side and mark_price; reject locally with `BYBIT_DEMO_SET_SL_DIRECTION_BUG` if wrong-side. Handle 34040 as success.

Pros: catches all callers.
Cons: doesn't fix the sniper's wrong logic — same bug regresses if a different path is added; per prompt Rule 3, "Fixing only the adapter without finding the caller that computed wrong SL" is FORBIDDEN.

### Option C — Multi-layer (recommended)

Three layers of defense:

1. **Sniper `_apply_trail_stop`**: wrong-side guard right after the existing SNIPER_TOO_CLOSE check. Logs SNIPER_WRONG_SIDE_GUARD and returns False. Root cause fix.
2. **Adapter `set_stop_loss` + `set_take_profit`**: defensive validate-and-reject. Reads current position via `get_position(symbol)`; rejects locally with a clear `BYBIT_DEMO_SET_SL_DIRECTION_BUG` / `BYBIT_DEMO_SET_TP_DIRECTION_BUG` tag. Last-mile defense — catches any future caller that bypasses the sniper guard.
3. **Adapter `set_stop_loss`**: handle ret_code 34040 as success (idempotent retry). Mirrors the existing pattern at `set_leverage:519` for ret_code 110043.

Pros: defense-in-depth (Rule 3 satisfied); also fixes ICPUSDT 34040; clear observability per layer; covers TP latent bug (audit ISSUE 1.7-A).
Cons: touches three files in one commit (within the same blast radius — all SL-side).

### Option D — Add gateway-level R5 (in addition to C)

Gateway is the central enforcement point. Adding R5 wrong-side check there would catch all callers AND keep the gateway's "single source of truth" pattern intact. But this is broader scope — the gateway is shared between bybit_demo and shadow modes; any change risks shadow regressions.

Skipped for this commit; can be added separately if operator wants gateway-level enforcement after observing how Option C performs.

## Recommendation

**Option C.** Sniper guard (root cause) + adapter defense (last-mile) + 34040 handling (different bug class). Tests at each layer.

## Implementation plan

Single atomic commit. Files modified:

1. `src/workers/profit_sniper.py:1505-1514` — extend the SNIPER_TOO_CLOSE check with a wrong-side guard. Log SNIPER_WRONG_SIDE_GUARD; return False.

2. `src/bybit_demo/bybit_demo_adapter.py:521-541` — add a precondition block at the top of `set_stop_loss`. Read `pos = await self.get_position(symbol)`; if pos exists and SL is on wrong side of pos.mark_price, log `BYBIT_DEMO_SET_SL_DIRECTION_BUG` and return False without calling Bybit. Mirror in `set_take_profit:543-563`. Also: catch ret_code 34040 in the existing TradingMCPError block and return True (idempotent success).

3. Tests: `tests/test_critical5_sl_wrong_side.py` with:
   - Sniper wrong-side guard (Sell + Buy parametrized)
   - Adapter SL wrong-side rejection (Sell + Buy)
   - Adapter TP wrong-side rejection (Sell + Buy)
   - Adapter 34040 idempotent success

## Open questions

None blocking. Existing 8 KATUSDT/RENDERUSDT/ICPUSDT alert events stay in the historical record. No SL state mutation needed — fresh sniper ticks will produce correct SL values immediately after deploy.
