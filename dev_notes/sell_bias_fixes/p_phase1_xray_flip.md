# PRIMARY Issue — Phase 1 Step P.1.5: XRAY Direction Flip Mechanism

Sources:
- `src/workers/strategy_worker.py:1604-1779` (XRAY flip gate)
- `src/analysis/structure/structure_engine.py:280-339` (rr_long/rr_short computation)
- `src/analysis/structure/models/structure_types.py:117-124` (StructuralPlacement dataclass)
- `src/core/thesis_manager.py:189` (THESIS_FLIP_PERSISTED)

Status: read end-to-end. Investigation only.

## 1. The XRAY Flip Is Code, Not LLM

Unlike APEX (which depends on DeepSeek model output), the XRAY flip is deterministic Python. Decision tree:

1. Compute `_ratio` based on direction:
   - `direction == "Buy"`: `_ratio = rr_short / rr_long` (if `rr_long > 0`)
   - `direction == "Sell"`: `_ratio = rr_long / rr_short` (if `rr_short > 0`)
   - Else: `_ratio = 0.0`
2. Read `_flip_threshold = settings.risk.xray_dir_flip_threshold_ratio` (default 3.0).
3. If `_apex_locked AND _ratio > _flip_threshold` → emit `XRAY_FLIP_SUPPRESSED_BY_LOCK`, set `_xray_flip_suppressed_by_lock=True`, DO NOT flip.
4. Else if `_ratio > _flip_threshold`:
   - Require dual structural levels (`long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price` all > 0). If missing → `XRAY_DIR_BLOCK` → skip trade.
   - Re-check post-flip conflict (flipped direction vs strong structural trend on weak setup). If conflict → `XRAY_DIR_FLIP_BLOCKED` → skip trade.
   - **Flip:** mutate `trade["direction"]`, SL, TP, `_apex_was_flipped=True`, `_flip_source="xray"`, `_xray_flip_ratio`, `_xray_flip_rr_long`, `_xray_flip_rr_short`.
   - Emit `XRAY_DIR_FLIP` (WARNING).
5. Else `_ratio <= _flip_threshold` → no flip; trade continues with original direction.

## 2. Where rr_long and rr_short Come From

`StructureEngine.analyze(...)` at `structure_engine.py:280-339`:

```python
long_pl  = _sl_engine.calculate(current_price, direction="long",
                                 support_levels, resistance_levels, market_structure, position_in_range)
short_pl = _sl_engine.calculate(current_price, direction="short", ...)
long_rr  = long_pl.rr_ratio if long_pl else 0.0
short_rr = short_pl.rr_ratio if short_pl else 0.0
```

The same `_sl_engine` is called twice with opposite direction labels. Each call returns a `StructuralPlacement` whose `rr_ratio` is computed from the distance from current price to the structural TP and SL for that direction.

Intuition (verified by reading the engine docs):
- `long_rr ≈ (resistance_distance) / (support_distance)` — distance to nearest resistance / distance to nearest support
- `short_rr ≈ (support_distance) / (resistance_distance)` — inverse

So `long_rr` and `short_rr` are functions of:
- How far current price is from the nearest support level (below)
- How far current price is from the nearest resistance level (above)

When current price is close to resistance (near top of range): `long_rr` small, `short_rr` large. When close to support (near bottom): `long_rr` large, `short_rr` small.

## 3. Live Evidence — XRAY Flip Asymmetry Today

10 sampled `XRAY_DIR_FLIP` events from today's logs:

| Symbol | Original | Flipped | rr_long | rr_short | ratio |
|--------|----------|---------|---------|----------|-------|
| ARBUSDT  | Buy  | Sell | 0.1 | 2.9 | 19.3× |
| ADAUSDT  | Buy  | Sell | 0.1 | 4.1 | 51.5× |
| CRVUSDT  | Buy  | Sell | 0.2 | 3.5 | 17.6× |
| NEARUSDT | Buy  | Sell | 0.2 | 4.2 | 26.1× |
| BLURUSDT | Buy  | Sell | 0.1 | 8.2 | 136.5× |
| GALAUSDT | **Sell** | **Buy** | 4.9 | 0.1 | 44.9× |
| HBARUSDT | Buy  | Sell | 0.0 | 5.6 | 556.0× |
| BLURUSDT | Buy  | Sell | 0.1 | 5.9 | 53.5× |
| MANAUSDT | Buy  | Sell | 0.1 | 7.4 | 123.5× |
| NEARUSDT | Buy  | Sell | 0.0 | 6.7 | 668.0× |

Distribution across all 19 XRAY_DIR_FLIP events today:
- Buy → Sell: **18 (94.7%)**
- Sell → Buy: **1 (5.3%)**

The asymmetry is dramatic — and it is driven entirely by the structural data: `rr_long ≪ rr_short` on 18 of 19 occasions. In every Buy→Sell case, the long R:R is between 0.0 and 0.2 (essentially zero) while the short R:R is between 2.9 and 8.2.

## 4. Interpretation — Where Does The Asymmetry Originate?

The XRAY flip is symmetric in code. The asymmetry must come from **the structural placement data itself**. Possible roots:

1. **Market state today**: prices are systematically near or above the nearest resistance levels, putting `(resistance - current_price)` near zero. This produces tiny `long_rr` values and large `short_rr` values. If the market shifted (e.g. into a downward leg approaching support), the asymmetry would flip the other way.

2. **Support/resistance detection bias**: the structural levels engine may identify more resistance levels above current price than support levels below, or be more sensitive to resistance proximity than support proximity.

3. **Scanner sampling bias**: signal generation tends to fire when momentum extends. If momentum extends UP, signals trigger near tops (Buy signals from brain into a price-near-resistance state), then XRAY's structural placement says "Buy here has terrible R:R, Sell has great R:R". The flip is mathematically correct *given the current price location*, but it means brain is consistently signaling Buy at the wrong part of the range.

4. **Range definition**: in ranging markets, "near resistance" is a stationary state for many coins for hours. If the scanner fires multiple times in this state, multiple Buy→Sell flips fire in sequence — each individually rational but collectively producing the 27/27 Sell-bias observed.

The data does not separately tell us which of (1)-(4) dominate. P.1.6 (regime detector validation) will help — if regime is mis-classified as ranging when it's actually trending up, the analysis breaks down differently.

## 5. The `xray_ratio=0.0x` Mystery (17/27 in the Spec Window)

The spec reported 17 of 27 DIRECTION_DECISIONs in the 2-h window had `xray_ratio=0.0x`. This is explained by lines 1618-1622:

```python
_ratio = 0.0
if direction == "Buy" and _sp.rr_long > 0:
    _ratio = _sp.rr_short / _sp.rr_long
elif direction == "Sell" and _sp.rr_short > 0:
    _ratio = _sp.rr_long / _sp.rr_short
```

If the chosen-direction RR is zero (which it often is on a Buy entry near resistance, where `rr_long = 0`), the entire `_ratio` computation is skipped. The ratio stays at 0.0, the XRAY flip gate never trips (0.0 < 3.0), and the trade proceeds. But if the trade was already flipped by APEX, `_xray_flip_ratio` was never populated by the XRAY path either — so the DIRECTION_DECISION log emits `xray_ratio=0.0x`.

In other words, `xray_ratio=0.0x` in DIRECTION_DECISION means **"XRAY did not handle this flip"** — and combined with `flipped=Y reason=apex_flip` it tells us the flip was driven by APEX (i.e. DeepSeek), not by XRAY. The spec's 17/27 number maps to APEX-driven flips in that window.

A more elegant invariant: when `xray_ratio > 0.0`, the XRAY gate ran on the trade (and may or may not have fired the flip). When `xray_ratio == 0.0`, either there was no XRAY data or the chosen-direction RR was zero (a degenerate case).

## 6. THESIS_FLIP_PERSISTED Persistence

When a flip happens (XRAY-driven), `thesis_manager.save_thesis` is called downstream (later in `_execute_claude_trade`). If `xray_flip_source` is set, line 189 emits:

```
THESIS_FLIP_PERSISTED | tid={trade_id} sym={symbol} source=xray ratio={r}
  rr_long={rrl} rr_short={rrs} orig_dir={orig} flipped_to={new}
```

Persisted to `trade_thesis` table (schema v28 columns from CALL_B Framing Fix Phase 1E, 2026-05-06). Backstop for the in-flight XRAY_DIR_FLIP log, ensuring the flip rationale survives even if log rotation purges the live emission.

## 7. The Coupling Between APEX And XRAY

APEX's `_apex_locked` plumbing (Issue 1 fix, 2026-05-11) gives XRAY a way to know when APEX has locked the direction. Otherwise the two systems are independent:
- APEX flip is driven by DeepSeek + confidence gate + (broken) RR boost.
- XRAY flip is driven by structural placement + ratio threshold.
- Both can fire on the same trade; whichever fires last writes `_flip_source`.

When APEX flips (`_flip_source` unset by APEX, since APEX doesn't write that field — only XRAY writes "xray") then XRAY flips again, `_flip_source` ends up "xray" and the DIRECTION_DECISION reason becomes `xray_flip` — even though APEX did the first flip. This is the docstring's promised `xray_flip_overrode_apex_flip` case, which the actual code does NOT distinguish (it collapses to `xray_flip`).

## 8. Findings Map

| Question | Answer |
|----------|--------|
| Where is XRAY flip computed? | `strategy_worker.py:1604-1779` |
| Is XRAY's logic Sell-biased intrinsically? | No — code is symmetric. The data feeding it is Sell-biased today. |
| Is the asymmetry due to market state? | Yes, primarily — current price is systematically near resistance levels on most flipped trades (`rr_long ≈ 0`, `rr_short` large). |
| What's the threshold? | `xray_dir_flip_threshold_ratio = 3.0` (config.toml:341). Today's ratios are 17×-668× — wildly above threshold. |
| Why do some DIRECTION_DECISION rows show xray_ratio=0.0? | The XRAY gate's ratio computation skipped (chosen-direction RR was 0 or no XRAY data). Those flips were APEX-driven. |
| Can XRAY override an APEX_DIR_LOCK? | Not since Issue 1 fix 2026-05-11 — emits `XRAY_FLIP_SUPPRESSED_BY_LOCK` instead. |

## 9. Operator-Facing Implications For Phase 2

- The **XRAY-driven Sell-bias is a market-condition artifact**, not a code defect. The ratio gate at 3× is firing on every trade where the chosen direction's R:R is near zero. The structural placement data is doing what it's designed to do.
- However, the flip-then-trade behavior means **brain's Buy signals are being structurally rationalized into Sell trades** — a mechanism the operator may or may not endorse.
- If the operator wants Buy entries near resistance to be SKIPPED rather than FLIPPED to Sell, Option 5 in Phase 2 (brain authority restoration) becomes relevant.
- If the operator wants the XRAY ratio gate to require both directions to have plausible R:R (not just opposite being >> chosen), an asymmetric-threshold option is possible — e.g. `flip only when rr_chosen > 0.5 AND rr_opposite > 3.0 * rr_chosen`.
- The threshold of 3.0× is empirically conservative on its own; what amplifies the impact is the combination with `rr_long ≈ 0` (any positive `rr_short` becomes infinity-relative).

## 10. Out-of-Scope Confirmation

- No code changes.
- No interaction with brain, Bybit execution, or Shadow.
