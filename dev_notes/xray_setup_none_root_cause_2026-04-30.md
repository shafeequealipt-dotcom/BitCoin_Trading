# XRAY `setup_type=none` — Root-Cause Diagnostic

**Date:** 2026-04-30 21:30 UTC
**Question:** Why does XRAY emit `setup_type=none` for ~30 of 50 watch_list coins every cycle, starving Layer 1D?
**Method:** parsed `XRAY_NONE_REASON` lines from cycles 21:00, 21:05, 21:10, 21:15, 21:20 + ran a direct probe of the FVG/OB/MS detectors on 6 NONE coins and 6 PASS coins (script: `scripts/xray_none_root_cause_probe.py`).

---

## Verdict (one sentence)

**XRAY's `BULLISH_FVG_OB` / `BEARISH_FVG_OB` branches require both an unfilled in-direction FVG AND a fresh in-direction OB within 2%/3% of current price — but in the current trending market, ~60% of watch_list coins have no in-direction FVG/OB *near* price, because the trend itself has already filled the demand/supply zones it would have needed.**

The classifier code is correct; the input pool is genuinely thin given current market conditions. There is no software bug.

---

## Evidence

### 1. The decision tree (structure_engine.py:760–810)

`BULLISH_FVG_OB` requires **all four** of:

```python
nearest_fvg.direction == "bullish"   AND not filled
nearest_ob.direction  == "bullish"   AND fresh
_bull_alignment()                    # direction=long AND (uptrend OR (ranging AND mtf>=0.55))
mtf_score_01 >= fvg_ob_min           # 0.50 in current config
```

`nearest_fvg` / `nearest_ob` come from `_find_nearest_fvg` / `_find_nearest_ob` (structure_engine.py:554–587), which **filter by direction first** and then enforce a **2%/3% distance window**:

```python
def _find_nearest_fvg(fvgs, price, direction):
    expected = "bullish" if direction == "long" else "bearish"
    for fvg in fvgs:
        if fvg.filled or fvg.direction != expected: continue
        if abs(fvg.midpoint - price)/price * 100 < 2.0:
            return fvg
    return None    # ← drives no_fresh_bullish_fvg / no_fresh_bearish_fvg
```

`_find_nearest_ob` mirrors this with a 3% window and `ob.fresh` check.

### 2. Aggregate first-failure tally across 30 NONE coins (cycle 21:20)

| First failure code | Count | Share |
|---------------------|-------|-------|
| `no_fresh_bullish_fvg` (incl. compound) | 14 | 47% |
| `no_fresh_bearish_fvg` (incl. compound) | 5  | 17% |
| `no_fresh_bearish_ob`  (alone first)    | 6  | 20% |
| `no_bearish_bos`                        | 4  | 13% |
| `no_bullish_bos`                        | 1  | 3%  |
| **Total**                               | 30 | 100%|

→ **64% of NONE failures are missing-FVG**, **20% missing-OB**, **16% missing-BOS**.

### 3. Direct probe — what's actually present in the kline data (200 H1 candles)

```
NONE coins                           bull_unfilled  bear_unfilled  bull_fresh_ob  bear_fresh_ob  scanner-log direction
BTCUSDT  (uptrend)                          0              2              0              1            long
ETHUSDT  (uptrend)                          0              2              1              1            long
SOLUSDT  (uptrend)                          0              2              1              2            long
XRPUSDT  (ranging)                          0              1              0              2            long
DOGEUSDT (downtrend)                        6              1              7              2            short
AAVEUSDT (downtrend)                        0              2              1              2            short

PASS coins                           bull_unfilled  bear_unfilled  bull_fresh_ob  bear_fresh_ob  resulting setup
BNBUSDT  (ranging)                          0              3              0              1            bearish_fvg_ob
ADAUSDT  (uptrend)                          1              1              1              2            bullish_fvg_ob
LINKUSDT (ranging)                          0              2              1              1            bearish_fvg_ob
DYDXUSDT (ranging)                          0              4              1              1            bearish_fvg_ob
BCHUSDT  (downtrend)                        0              2              0              3            bearish_fvg_ob
NEARUSDT (downtrend)                        0              2              1              3            bearish_fvg_ob
```

### 4. Engine-style direction-filtered nearest (within 2%/3% of price)

```
                eng_fvg[Long?/Short?]    eng_ob[Long?/Short?]    log direction → required
BTCUSDT (NONE)         L:N / S:Y                L:N / S:Y           long  → BOTH long inputs MISSING
ETHUSDT (NONE)         L:N / S:Y                L:Y / S:N           long  → long FVG MISSING
SOLUSDT (NONE)         L:N / S:Y                L:Y / S:Y           long  → long FVG MISSING
XRPUSDT (NONE)         L:N / S:Y                L:N / S:Y           long  → BOTH long inputs MISSING (BoS branch then matched, also fails)
DOGEUSDT(NONE)         L:Y / S:Y                L:Y / S:N           short → short OB MISSING
AAVEUSDT(NONE)         L:N / S:N                L:Y / S:N           short → BOTH short inputs MISSING

DYDXUSDT(PASS)         L:N / S:Y                L:Y / S:Y           short → BOTH short inputs PRESENT ✓
LINKUSDT(PASS)         L:N / S:Y                L:Y / S:Y           short → BOTH short inputs PRESENT ✓
NEARUSDT(PASS)         L:N / S:Y                L:Y / S:Y           short → BOTH short inputs PRESENT ✓
```

The pattern is exact: **PASS coins have both an in-direction unfilled FVG and an in-direction fresh OB within 2%/3% of price; NONE coins are missing one or both**.

---

## Root cause (mechanical)

**`_find_nearest_*` is direction-locked AND distance-bounded.** When `suggested_direction` (set by `market_structure.structure` — `uptrend → long`, `downtrend → short`, `ranging → fallback to better RR direction`) does not have a fresh, unfilled in-direction zone within 2%/3% of price, the FVG_OB branch fails by construction.

## Root cause (structural / market-condition)

The 30 NONE coins fall into three groups:

1. **Trend-extension exhaustion** (BTCUSDT, ETHUSDT, SOLUSDT, ADA-like uptrending coins): `structure=uptrend` forces `direction=long`, but every bullish FVG that existed was filled as price rallied through it. Result: `bull_unfilled=0` on the kline data → `_find_nearest_fvg("long")` returns None.
2. **Counter-direction zones present, but unusable** (DOGEUSDT, AAVEUSDT-like): in-direction FVGs/OBs exist but are >2%/>3% away from current price, or only the opposite-direction ones are within range.
3. **Range with no fresh BoS** (XRPUSDT, PLUMEUSDT, EGLDUSDT, ALICEUSDT, SANDUSDT): no FVG_OB AND no major break-of-structure → falls to `no_bullish_bos` / `no_bearish_bos`.

The classifier **is doing exactly what it was designed to do**: refuse to label a directional setup when the structural prerequisites for that direction aren't sitting next to current price. In strong trending regimes (or directly after a strong move), the trend consumes its own setups — that's a market truth, not a code defect.

## Why scanner pass count tracks this so closely

- `pass_xray = 50 − fail_no_xray − fail_setup_none ≈ 18–20` per cycle.
- Of those 18–20, only those with **STRONG/GOOD ensemble consensus** (1–2 / cycle) survive to `qualified=1` and become a Scanner pick. So the headline starvation is a two-layer cascade — XRAY's 60% setup_type=none is the upstream half.

---

## Levers (ranked by impact-to-risk ratio)

### A — widen `_find_nearest_*` distance windows  *(low-risk, biggest single lift)*

Current: FVG within 2%, OB within 3%. In low-volatility coins (atr_pct ≈ 0.40–0.60% in our probe), 2% = 3–5 ATR. In high-volatility coins (atr_pct ≈ 1.0–1.3%), 2% = 1.5–2 ATR. The fixed % is too tight for low-vol, too loose for high-vol.

**Replacement:** `dist_threshold_pct = max(2.0, k * atr_pct)` where `k ≈ 3` for FVG, `k ≈ 4` for OB. Or just hard-bump 2.0→3.0% / 3.0→4.0% to start.

Files: `src/analysis/structure/structure_engine.py:567` (FVG), `:585` (OB). Make the threshold a `setup_types.*` config knob.

Expected lift: would bring DOGEUSDT's bear OB (currently >3% away) into range → +6 coins / cycle (the `no_fresh_bearish_ob` group).

### B — relax `structural_break_require_retest`  *(low-risk, narrow lift)*

Currently `structural_break_require_retest=true` forces `last_bos.significance == "major"`. The 5 BoS-fail coins (XRPUSDT, PLUMEUSDT, EGLDUSDT, ALICEUSDT, SANDUSDT) likely have a non-major BOS that's being rejected.

**Change:** `structural_break_require_retest = false` in config.toml:1037.

Expected lift: +5 coins / cycle (BoS group).

### C — fall back to direction-agnostic nearest as a tier-2 setup  *(medium-risk, biggest theoretical lift)*

When `_find_nearest_fvg(direction)` returns None, scan the unfiltered list and emit a new SetupType (e.g. `BULLISH_FVG_OB_COUNTER` / `BEARISH_FVG_OB_COUNTER`) with confidence ×0.7. The TradeScorer + ensemble already weight by confidence, so low-quality counter-setups wouldn't auto-promote to STRONG.

Expected lift: +14 coins / cycle (the FVG-missing group BTCUSDT/ETHUSDT/SOLUSDT/etc.) but they would surface as low-confidence setups that probably get filtered at the consensus stage.

### D — accept FVG-only or OB-only setups  *(medium-risk, dilutes setup quality)*

Drop the AND between FVG and OB; emit `BULLISH_FVG_ONLY` / `BULLISH_OB_ONLY` with reduced confidence. Risk: lower-quality setups muddy ensemble votes.

### E — config-only quick win: set `setup_types.fvg_ob_min_confluence` lower  *(no impact on this issue)*

Already at 0.5 (down from 0.7). Some NONE coins fail this gate too (e.g. `mtf_score=0.40<fvg_ob_min=0.50`), but they're a small subset. Lowering to 0.4 helps maybe 2 coins / cycle.

---

## Recommended next step

**Do A first, in isolation.** It's a one-line config knob (after refactor), measurable in 1–2 cycles, reversible. If A alone doesn't move `pass_xray` from 20 → 25+, layer B on top. C and D should be considered only if A+B together don't restore 5–8 picks/cycle — they change setup semantics and need ensemble re-tuning.

Skip option E — already tuned.

---

## Files / locations

- Classifier decision tree: `src/analysis/structure/structure_engine.py:680–840`
- Direction-filtered nearest finders: `src/analysis/structure/structure_engine.py:554–587`
- Miss-reason diagnostic: `src/analysis/structure/structure_engine.py:843–1010`
- Worker loop emitting `XRAY_NONE_REASON`: `src/workers/structure_worker.py:130–195`
- Config knobs: `config.toml:1035–1046` (`[analysis.structure.setup_types]`)
- FVG detector: `src/analysis/structure/fair_value_gap.py:32–170`
- OB detector: `src/analysis/structure/order_blocks.py:26–185`
- Diagnostic probe: `scripts/xray_none_root_cause_probe.py`

## Probe results reproduction

```bash
cd /home/inshadaliqbal786/trading-intelligence-mcp
PYTHONPATH=. .venv/bin/python scripts/xray_none_root_cause_probe.py
```
