# H4 Phase 2 — Operator Report: Why brain re-proposes blocked symbols

## h1. Issue restated

In a 5h07m baseline (2026-05-16 07:26 → 12:33 UTC) **31 of 54 (57.4 %) APEX directives were blocked by the J6 reentry learning gate with `reason=same_conditions`**. Brain re-proposed XRPUSDT 8 times, LINKUSDT 6 times, SEIUSDT 5 times across the window. Operator (2026-05-16): *"I don't want this much rejection — find the root cause."* This report presents the root cause and the recommended fix.

## h1. Evidence (the headline)

### h2. Bucket distribution of the 31 blocks

| Bucket | Count | Note |
|---|---|---|
| (b) Categorically identical but contextually different — gate too coarse | **31 (100 %)** | The gate's `(regime, setup, direction)` equivalence matches by coincidence of categorical bucket; underlying market context has typically moved |
| (c) Prior loss > 16h old (no recency clamp) | **8 (26 %)** | DYDX cites a 60h-old loss; SKR cites a 17h-old loss; ARB cites a 34h-old loss; LDO cites a 16h-old loss |
| (b') Prior loss magnitude under $2 (no magnitude floor) | **8 (26 %)** | ARB $-0.04 (4 cents!), AVAX $-1.03 × 3, SKR $-1.57 × 4 — all sub-noise losses, indefinite blockers |
| (a) Truly identical AND brain at fault alone | 0 | All rows ALSO fall in (b) — there is no row where brain visibility alone would fix it |
| (d) Malformed thesis | 0 | None |

### h2. Most damning examples

- **DYDXUSDT** blocked on a **60-hour-old** prior $-8.64 loss.
- **ARBUSDT** blocked on a **34-hour-old prior $-0.04 loss** (a 4-cent loss is below taker-fee noise).
- **SKRUSDT** blocked **4 times** on a 17-hour-old $-1.57 loss (also sub-noise).
- **LDOUSDT** blocked on a 16-hour-old $-32.88 loss (loss is material but market has moved over 16 h).
- 22 of 31 blocks (71 %) cite a single 2-second exit-cohort event at 07:41 (XRP/LINK/SEI/AVAX all bumped together) — that one moment is responsible for the majority of the rejection rate for the next 5 hours.

### h2. Anatomy

The gate's primitive (`src/core/trade_coordinator.py::check_reentry_learning_gate`, lines 1478-1599) runs:

```sql
SELECT entry_regime_at_open, entry_setup_type, direction, actual_pnl_usd
FROM trade_thesis
WHERE symbol = ?
  AND status = 'closed'
  AND actual_pnl_usd < 0
ORDER BY closed_at DESC
LIMIT 1
```

then blocks if the current `(regime, setup, direction)` triple matches the prior triple exactly. There is **no time bound, no magnitude filter, no market-shift escape**. A single losing thesis blocks indefinitely.

The full anatomy is in `h4_phase1_gate_anatomy.md`. The full rejection trace is in `h4_phase1_rejection_trace.md`. The full root-cause analysis is in `h4_phase1_synthesis.md`.

## h1. Options table

| Option | Description | Touches | Aim impact | Drop in rejection rate forecast | Recommend? |
|---|---|---|---|---|---|
| **R1 — Gate calibration** | Add recency clamp + magnitude floor + ATR-shift escape to `check_reentry_learning_gate` | `src/core/trade_coordinator.py` (one method), `config/settings.py`, `config.toml` | All 4 aim-bias questions YES (frequency RISES) | 57 % → ~10-15 % | **YES — primary** |
| R2 — Broaden candidate pipeline | Layer 1B/1C diversity gate | `src/strategies/scanner.py`, `src/strategies/ensemble.py` | Aim-neutral but **OUT OF SCOPE** per spec | Tangential | No |
| R3 — Brain selection bias fix | Rebalance category-split / voter enrichments | `src/brain/strategist.py` enrichments | Aim-positive but **OUT OF SCOPE** per spec | Tangential | No |
| R5 — Rejection feedback to CALL_A | Persist rejections + render in prompt | new repo, `strategist.py`, schema migration | Aim-positive; addresses brain re-selection only | 57 % → ~30-40 % (alone); ~10 % (after R1) | Optional secondary if R1 leaves residual |
| R6 — R1 + R5 combined | belt-and-suspenders | both above | Aim-positive | 57 % → < 10 % | Defer to post-R1 verification |

## h1. Recommendation

**Implement R1 (gate calibration) only.** Three additive escape conditions inside `check_reentry_learning_gate`:

1. **Recency clamp** — `settings.apex.reentry_learning_gate_lookback_hours` (default 6). Losses older than the window do not trigger blocks (filter the SQL with `closed_at > now() - lookback_hours`).
2. **Magnitude floor** — `settings.apex.reentry_learning_gate_min_loss_usd` (default 5.0). Filter SQL with `actual_pnl_usd < -min_loss_usd`. Below-noise losses do not trigger blocks.
3. **ATR-shift escape** — `settings.apex.reentry_learning_gate_atr_drift_pct` (default 25.0). Allow re-entry when current ATR has shifted by ≥ threshold % since the prior loss's `entry_volatility`. Reads from existing TACache + schema v26 `entry_volatility`.

The three escapes are additive: ANY one passes ⇒ allow. Combined, they preserve the original protective intent (block genuinely-fresh + meaningful + same-context re-entries) while eliminating the over-blocking pattern.

**Why R1 alone (not R6):** Phase 1 evidence shows 100 % of blocks fall in bucket (b) which R1 directly addresses. Adding R5 on top is non-trivial (new table, schema migration, prompt growth, additional latency on CALL_A construction) for limited incremental benefit. After R1 deploys and verifies, if rejection rate remains above 15-20 % over 24h, layer R5 in a follow-on cluster.

## h1. Aim-bias verdict (4 questions)

| Question | Verdict |
|---|---|
| 1. Trade frequency preserved? | **YES — RISES.** Bucket (b'+c) blocks (~16 of 31) vanish entirely. Bucket-(b) blocks with valid ATR drift also pass. Net: ~5-10 more open-side trades per hour. |
| 2. Aggression preserved? | **YES — UNLEASHED.** Removes artificial brake; brain's identical-setup re-entries pass when conditions truly drifted. |
| 3. Decision speed or quality? | **YES — QUALITY.** Re-entries on legitimately-shifted markets execute. No latency impact (this is gate calibration, not pipeline speed). |
| 4. Passive-close advantage preserved? | **YES — UNTOUCHED.** Gate is open-side. Watchdog / sniper / time-decay-SL paths unchanged. |

All four YES. Aim is fully preserved and arguably *better served* than before.

## h1. Trial behaviour (Rule 16)

After R1 lands:

- The gate continues to block `(regime, setup, direction)` triple-match BUT only when ALL of:
  - Prior loss closed within `lookback_hours` (default 6).
  - Prior loss exceeded `min_loss_usd` (default $5).
  - ATR has not drifted by `atr_drift_pct` (default 25 %) since the prior loss.
- New observability events fire whenever an escape passes:
  - `GATE_RECALIBRATION_ALLOW | sym=... reason=lookback_expired prior_loss_age_h=... prior_pnl=... | ctx()`
  - `GATE_RECALIBRATION_ALLOW | sym=... reason=loss_below_floor prior_pnl=-1.03 floor=-5.00 | ctx()`
  - `GATE_RECALIBRATION_ALLOW | sym=... reason=atr_drift_passed atr_then=... atr_now=... pct=...% threshold=... | ctx()`
- Existing `REENTRY_LEARNING_GATE action=block` event is retained for backward compat (rolled into `GATE_RECALIBRATION_BLOCK` synonym if the operator prefers).
- Brain proposes XRP / LINK / SEI again → most pass the gate (if ATR drifted). Trade frequency rises. Win-rate must hold (R1 is calibration, not gate-removal — the protective intent of "block genuinely fresh same-context revenge trades" remains).

## h1. Verification metrics (24h soak after deploy)

| Metric | Baseline (today, 5h) | Target after R1 |
|---|---|---|
| `same_conditions` block count | 31 in 5h (~6.2/hr) | < 5/hr |
| Rejection rate | 57.4 % | < 25 % |
| XRP/LINK/SEI re-selection top-3 | 8 / 6 / 5 | ≤ 2 each |
| `GATE_RECALIBRATION_ALLOW` total | 0 | ≥ 25 over 24h (the trapped-by-R1-fix cases) |
| Trade frequency (BRAIN_DO_TRADE rsn=ok / hr) | 4.5/hr | ≥ 5/hr |
| Win rate (24h closures) | 78.6 % session | ≥ 50 % (HOLD) |
| CALL_A median latency | 102 s | Unchanged |
| DB cascade events | 0 | 0 |
| Shadow path | working | working |

## h1. Open questions for operator

Q-1. **Default `lookback_hours`**: I propose 6 h. Operator may prefer 2 h (aggressive — frees more trades but reduces protection from genuinely-fresh losses) or 24 h (conservative — closer to existing behaviour, more selective unblocking). Default 6 h balances aim with prudence based on observed cohort timing (the 07:41 cohort would clear at 13:41 under a 6 h clamp).

Q-2. **Default `min_loss_usd`**: I propose $5. Taker fee on a $100 trade is ~$0.06; slippage variance commonly ±$1; a "real learning signal" should exceed at least 1-2 ATR-scaled R losses. $5 is a reasonable lower bound; $10 if more conservative.

Q-3. **Default `atr_drift_pct`**: I propose 25 %. This is the threshold above which ATR-shift is operationally meaningful (large enough that the prior thesis's volatility assumption is invalidated). Operator may prefer 15 % (more permissive) or 40 % (more conservative).

Q-4. **Backward-compat log naming**: keep `REENTRY_LEARNING_GATE action=block` for the block path AND add `GATE_RECALIBRATION_ALLOW` for the new escapes — or rename both into a `GATE_RECALIBRATION_*` family? I'll keep both for backward compat unless operator says otherwise.

## h1. Default proceed unless redirected

Per operator instruction this session ("work without stopping for clarifying questions"), I will proceed to **H4 Phase 3 implementation with the recommended R1 option and the defaults proposed above (6 h / $5 / 25 %)**. Operator can redirect any default after seeing the implementation diff.
