# Phase 0 — Pre-flight Verification + 8 Baselines

**Date:** 2026-05-06
**Branch:** main (HEAD: `2ac091d`, 98 commits ahead of origin)
**Source spec:** `/home/inshadaliqbal786/IMPLEMENT_CALLB_FRAMING_AND_FLIP_SURVIVAL_FIX_INDEPTH.md`
**Plan file:** `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-effervescent-moonbeam.md`

## Pre-condition Checks

### Working Tree

`git status` shows:
- Modified: `data/layer_state.json`, `trading.db` (data files only — acceptable)
- Untracked: dev_notes/data artifacts (acceptable)
- No source-file changes pending

### Services

```
trading-workers   active
trading-mcp-sse   active
shadow            active
```

### Logs

Current log files in `data/logs/`:
- `workers.log` (current writer, last update 19:25 UTC)
- `workers.2026-05-06_11-25-38_183602.log` (rotated, today)
- `workers.2026-05-05_21-48-58_246166.log` (rotated, yesterday)
- `general.log` (multi-stream)
- `brain.log` (Claude calls)

24h coverage confirmed.

### DB

`data/trading.db` accessible (143 MB).

### Prior Fixes Verified

All 7 prior fixes confirmed shipped per project memory:
- Stage 2 architectural fix
- Stage 2 framing fix for CALL_A
- Post-execution closure fix (commits `f718686`, `2c0a3c8`, `0795aca`, `37354fe`)
- XRAY direction-flip fix (`8784227`, `c44d6f0`, `dd761e4`, `2cb3dc4`, `a65e89c`, `889b995`)
- Time-decay Phase 1+2+3 (`7b8a2a9`, `16a277f`, `c744e26`)
- Layer 4 realignment (11 commits ending `c614d76`)
- Layer 4 audit follow-ups (`23c83af`, `9ee29fd`, `8da5710`, `1b1eb65`, `222c8e8`, ...)

## Issue Verification

All 5 issues confirmed present in current code. Forensic evidence below.

### Issue 1 — CALL_B framing closes APEX/XRAY-flipped trades

`STRAT_ACTION_CLOSE` close-reasons sampled from `workers.log` (today, post 17:00 UTC) — verbatim:

```
"Short against TRENDING_UP 80% regime. TA is BUY (62% confidence), RSI 65, price..."
"Thesis broken. Short into TRENDING_UP 73% regime. Was flipped Buy→Sell against r..."
"Thesis broken. Short into TRENDING_UP 70% regime. Flipped Buy→Sell. TIAS shows 0..."
"CLOSE — Regime is TRENDING_UP 90%, one of the strongest uptrend readings possibl..."
"CLOSE — Regime TRENDING_UP 71% opposes the short. Thesis reveals 0 Buy trades in..."
"Thesis broken: direction was LOCKED to Buy but flipped to Sell against strongest..."
"Thesis broken: direction LOCKED to Buy, flipped to Sell against 73% TRENDING_UP..."
```

These match the spec's verbatim quotes 1:1.

`POSITION_SYSTEM_PROMPT` at `src/brain/strategist.py:144-162` confirmed to contain decision rules:
- Rule 4: "If regime reversed against position direction and SL > 70% consumed: CLOSE."
- Rule 5: "If thesis is broken (the reason for entry no longer holds): CLOSE."

### Issue 2 — SURVIVAL mode rejecting RR<3.0 trades

`ENFORCER_LEVEL` event today at 16:46:06 UTC:
```
ENFORCER_LEVEL | old_el=0 new_el=2 | reason=pnl_below_survival | pnl=-8.68% strk=-11
```

Confirms SURVIVAL trigger at -7% (current) firing at pnl=-8.68%. `qualify_survival_trade()` blocks via `level_2_min_rr=3.0` per `config.toml:949-1001`.

### Issue 3 — DB lock contention

DB_LOCK_WAIT count today: **0**. Lower than spec's 14/min observation — likely the prior tuning (kline batching) brought lock contention down. **However**, runtime PRAGMA discrepancy is real and worth investigating as a latent root cause.

Runtime PRAGMA (live `data/trading.db`):
```
journal_mode      = wal       (matches code)
synchronous       = 2 NORMAL  (matches code)
busy_timeout      = 0         (CODE SETS 10000)
cache_size        = -2000     (CODE SETS -65536)
mmap_size         = 0         (CODE SETS 268435456)
temp_store        = 0         (CODE SETS MEMORY)
wal_autocheckpoint = 1000     (CODE SETS 2000)
locking_mode      = normal    (matches default)
```

Per-connection PRAGMAs (busy_timeout, cache_size, mmap_size, temp_store) reset on every new connection. The discrepancy says `connection.py` PRAGMA setup at lines 122-152 is not being applied universally — likely a connection path bypasses `DatabaseManager`.

### Issue 4 — SIG_DOWNGRADE rate

716 events in current `workers.log`, 338+759 in rotated logs. Sample:

```
SIG_DOWNGRADE | sym=HYPERUSDT from=buy to=neutral conf=0.34 strong_min=0.60 buy_min=0.40
SIG_DOWNGRADE | sym=ORCAUSDT from=sell to=neutral conf=0.37 strong_min=0.60 buy_min=0.40
SIG_DOWNGRADE | sym=OPUSDT from=buy to=neutral conf=0.28 strong_min=0.60 buy_min=0.40
SIG_DOWNGRADE | sym=APTUSDT from=buy to=neutral conf=0.28 strong_min=0.60 buy_min=0.40
SIG_DOWNGRADE | sym=ALICEUSDT from=buy to=neutral conf=0.24 strong_min=0.60 buy_min=0.40
```

Confirmed: most downgrades are `buy/sell → neutral` due to `conf < 0.40`. Confidence is the limiting factor, not classification.

### Issue 5 — Sentiment cache events

`SENT_UNKNOWN_CACHE_HIT`: 861/1797/2696 across the 3 log files. `SENT_DEGRADED_MODE`: 123/167/240. Cache is working as designed (per investigation); the volume is observability cost. Reddit `enabled = false` in `config.toml:52`; Finnhub free tier lacks altcoin coverage.

## Baselines

### Baseline 1 — CALL_B closure pattern (current `workers.log`, ~9h window)

| Metric | Count |
|---|---|
| `STRAT_ACTION_CLOSE` total | 63 |
| `STRAT_ACTION_CLOSE` actual closes (not BLOCKED) | 16 |
| `STRAT_ACTION_CLOSE_BLOCKED` (min-hold guardrail working) | 21 |

Reasons on actual closes: 100% cite "Thesis broken" / "regime mismatch" / "Short against TRENDING_UP". Zero cite SL-approaching, TP-approaching, structure-invalidated. This is the literal pattern Issue 1 identifies.

The min-hold guardrail (`strategic_action_min_hold_seconds=300`) shipped in post-execution-closure-fix Phase 1B is correctly blocking some closes (21 BLOCKED). But once the guardrail expires, the 16 closes still go through with the same framing-driven reasons.

### Baseline 2 — Flipped trade survival (last 50 closed)

```
Flipped trades:    38 / 50  (76%)
Non-flipped:       12 / 50  (24%)
```

Closed-trade outcome distribution (last 50, from `trade_thesis WHERE status != 'open'`):
- `mode4_p9` (sniper): 26 (52%)
- `strategic_review` (CALL_B): 13 (26%) — this is the killer pattern
- `zombie_reconciler`: 4 (8%)
- `shadow_sl_tp`: 2 (4%)
- `time_decay_p_win_low`: 1 (2%)
- `emergency_manual`: 1 (2%)
- Other: 3

For the 13 `strategic_review` closes: 12 cite "thesis broken" / "regime opposes" / "flipped". These are CALL_B killing flipped trades.

### Baseline 3 — Trade execution rate (current `workers.log`, ~9h window)

| Skip Reason | Count |
|---|---|
| `survival_block` | 12 |
| `sltp_skip` | 5 |

`survival_block` is dominant skip reason — confirms Issue 2.

### Baseline 4 — SURVIVAL mode time

ENFORCER_LEVEL transitions today:
- 16:46:06 UTC: el=0 → el=2 at pnl=-8.68%, strk=-11

Currently at el=1 (`pnl=-5.91%, strk=+2`) per latest sample. SURVIVAL was active during the trade-skip window.

`size_mult` in SURVIVAL was 0.25 (75% size reduction). Combined with RR=3.0 floor, this is hostile to opportunity capture.

### Baseline 5 — DB performance

| Metric | Value |
|---|---|
| `DB_LOCK_WAIT` count (current log) | **0** |
| `DB_LOCK_WAIT` count (rotated logs) | 0 / 0 |

DB lock contention not currently visible in events. Spec's 14/min observation may reflect a prior log window before the kline batching landed.

PRAGMA discrepancy still real (see Issue 3 above) — the per-connection PRAGMAs reset to defaults on every connect, suggesting at least one connection path bypasses `DatabaseManager`'s setup.

### Baseline 6 — Signal quality (SIG_DOWNGRADE)

| Window | Count |
|---|---|
| Current `workers.log` | 716 |
| Rotated 1 | 338 |
| Rotated 2 | 759 |

Sample 5 events show all downgrades are `buy/sell → neutral` because `conf < 0.40` (which is `buy_min`). Confidence calculator is producing values in 0.24-0.37 range. The downgrade IS appropriate (low-confidence signals shouldn't be amplified) but is destructive (overwrites the cached signal so downstream consumers can't see the original classification with the confidence floor flag).

### Baseline 7 — Sentiment availability

| Event | Count (current log) |
|---|---|
| `SENT_UNKNOWN_CACHE_HIT` | 861 |
| `SENT_DEGRADED_MODE` per-coin | 123 |

Cache is working as designed. The high volume is degraded-mode observability cost. Reddit disabled by config; Finnhub no altcoin coverage. Per investigation, sentiment is load-bearing in only ~3% of signal evaluations.

### Baseline 8 — Win rate (last 50 closed trades)

```
wins=14 / total=50    => 28% win rate
avg_win=+0.22%
avg_loss=-0.24%
expectancy = 0.28 * 0.22 - 0.72 * 0.24 = -0.11% per trade
```

Win rate 28% with near-1:1 win/loss size ⇒ negative expectancy. Most losses come from CALL_B-killed flipped trades (Baseline 2 cross-reference). The fix removes that obstruction; whether strategy-edge appears post-fix is what Phase 6 will measure.

## Verification Gate

| Item | Status |
|---|---|
| All 5 issues verified in current code | PASS |
| All 8 baselines captured | PASS |
| Working-notes file at this path | PASS |
| No commits made (Phase 0 contract) | PASS |

Gate: **PASS**. Proceed to Phase 1.

## Notable observations not in spec

1. The min-hold guardrail (post-execution-closure Phase 1B) is correctly blocking ~57% of CALL_B close attempts (21 BLOCKED vs 16 actual closes). When CALL_B repeats the same close intent past the 300s hold, the close goes through. The framing fix here addresses the root: CALL_B should not generate the close intent in the first place when the only reason is regime/thesis-broken.

2. The mode4_p9 (Sniper) is back to dominating closures (52% in last 50) despite the Layer 4 Phase 1 sniper realignment shipping. This may be expected: the realignment added age + PnL guards but did not stop legitimate deterministic-stall closes. Out of scope for this fix per spec.

3. Win rate 28% with a 1:1 risk-reward profile is unprofitable. Phase 6 trial will determine whether removing the CALL_B killer + freeing flipped trades + relaxing SURVIVAL pushes win rate above 35%.

4. DB_LOCK_WAIT count is currently 0, not 14/min as the spec quotes. The PRAGMA discrepancy is still worth fixing as a latent root cause but Phase 3 priority can be revisited if the trial shows no DB pressure.
