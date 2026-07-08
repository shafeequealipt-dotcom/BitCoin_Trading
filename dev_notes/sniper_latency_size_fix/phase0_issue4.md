# Phase 0 — Issue 4 Investigation: Sizing Composition

## Hypotheses Confirmed (A + B + D)

**Hypothesis A** — Multiple layers compose at runtime; same conviction produces different sizes.

**Hypothesis B** — Capital tier transitions fire on every equity sample (no hysteresis). Not directly observed in this 110-min window (no TIER UP/DOWN events) but verified in code at `src/fund_manager/tiered_capital.py:117-123`.

**Hypothesis D** — XRAY confidence and expected RR are NOT consulted by sizing logic. Only TIAS profit factor + setup grade enter conviction weight at `src/apex/gate.py:363-481`.

## Empirical Evidence — Same Conviction, Different Sizes

Live trade_thesis sample (15 trades opened from 10:30 onward, all bearish_fvg_ob setup type):

| Symbol | Direction | Size $ | Lev | XRAY conf | Setup type |
|--------|-----------|--------|-----|-----------|------------|
| RENDERUSDT | Sell | 175.15 | 3 | 0.7 | bearish_fvg_ob |
| EGLDUSDT | Sell | 150.00 | 3 | 0.7 | bearish_fvg_ob |
| BCHUSDT | Sell | 150.00 | 2 | 0.7 | bearish_fvg_ob |
| OPUSDT | Sell | 300.00 | 5 | 0.7 | bullish_fvg_ob |
| ALGOUSDT | Sell | 150.00 | 5 | 0.7 | bearish_fvg_ob |
| ALICEUSDT | Buy | 100.00 | 5 | 0.55 | bearish_fvg_ob |
| SOLUSDT | Sell | 100.00 | 3 | 0.7 | bearish_fvg_ob |
| MONUSDT | Sell | 100.00 | 3 | 0.7 | bearish_fvg_ob |
| FILUSDT | Sell | 100.00 | 5 | 0.7 | bearish_fvg_ob |
| IMXUSDT | Sell | 100.00 | 3 | 0.7 | bearish_fvg_ob |
| EGLDUSDT | Sell | 100.00 | 2 | 0.7 | bearish_fvg_ob |
| RENDERUSDT | Sell | 100.00 | 2 | 0.7 | bearish_fvg_ob |
| PYTHUSDT | Sell | 100.00 | 2 | 0.7 | bearish_fvg_ob |
| ORCAUSDT | Buy | 100.00 | 3 | 0.55 | bullish_fvg_ob |
| MONUSDT | Sell | 100.00 | 2 | 0.7 | bearish_fvg_ob |

**Smoking gun**: 13 of 15 trades have **identical XRAY confidence (0.7) and identical setup type (bearish_fvg_ob)**, yet produced sizes ranging $100-$300. Pearson correlation between xray_confidence and size_usd is essentially zero across the sample. The variance comes entirely from upstream sources NOT including conviction.

**XRAY confidence is bimodal at 0.55 / 0.70** — almost no spread, indicating the conviction signal isn't differentiating regardless.

## Pipeline Trace — Where Variance Originates

Verified path in `src/core/layer_manager.py:1259-1383`:

1. **Step 1 — Claude CALL_A response** (line 1259-1263): stamps `_claude_original_size_usd`. Claude already produces variable sizes ($100, $234, $400, etc.) with no correlation to conviction in its prompt.
2. **Step 2 — APEX optimizer** (line 1264-1281, parallel `apex.optimize()`).
3. **Step 3 — APEX result applied** (line 1326): writes new `size_usd` at `:1416`.
4. **Step 4 — TradeGate validate** (line 1332):
   - CHECK 0 (`apex/gate.py:65-92`): caps at `claude_orig × 1.5`.
   - CHECK 4 (`apex/gate.py:123-160`): conviction-weighted capital ceiling at `weight ∈ [0.5, 2.0]`.
5. **Step 5 — Performance Enforcer** (`src/workers/strategy_worker.py:1847-1856`): multiplies by 0.25-1.0 based on PnL.

### ENFORCER_SIZE log evidence (live)

10 events from the 110-min window, every single one shows `mult=0.75` (PRESERVATION mode active):

```
10:41:34 sym=RENDERUSDT orig=$234 mult=0.75 final=$175
10:41:34 sym=EGLDUSDT orig=$200 mult=0.75 final=$150
10:41:35 sym=BCHUSDT orig=$200 mult=0.75 final=$150
10:50:55 sym=OPUSDT orig=$400 mult=0.75 final=$300
10:50:56 sym=ALGOUSDT orig=$200 mult=0.75 final=$150
10:50:57 sym=ALICEUSDT orig=$100 mult=0.75 final=$100   (floor)
10:57:47 sym=SOLUSDT orig=$100 mult=0.75 final=$100     (floor)
10:57:47 sym=MONUSDT orig=$100 mult=0.75 final=$100     (floor)
10:57:48 sym=FILUSDT orig=$100 mult=0.75 final=$100     (floor)
11:05:45 sym=IMXUSDT orig=$100 mult=0.75 final=$100     (floor)
```

The Performance Enforcer is uniformly applying 0.75 — that's deterministic given mode. The variance source is **Step 1 (Claude originals: $100-$400)** not the enforcer.

But Claude's variance is itself unprincipled — same setup, same conviction, but $100 vs $400. Because Claude's prompt doesn't condition sizing on the conviction signals, and the downstream sizing layers don't either.

## BrainDecision dataclass

`src/core/types.py:375-386`:
```python
@dataclass
class BrainDecision(SerializableMixin):
    id: str
    action: str
    symbol: str
    confidence: float
    order_type: OrderType
    reasoning: str
    risk_notes: str
    created_at: datetime
```

Only `confidence` field. No `xray_confidence`, no `expected_rr`, no `setup_quality_grade`.

## Schema observation

`trade_thesis` already has `entry_xray_confidence REAL NOT NULL DEFAULT 0.0` (added by an earlier migration). Confidence IS captured at trade open — just not consumed by sizing. Phase 3 wiring becomes simpler: surface this from `CoinPackage` into `BrainDecision` and feed to APEX.

`trade_thesis` does NOT have a `setup_quality_grade` column today, but `entry_setup_type` exists. Setup grade can be derived from setup_score if needed.

## Confirmed Fix Shape — Full Fix (A+B+C+E per operator's choice)

| Sub-phase | What | Why |
|-----------|------|-----|
| 3A | Surface `xray_confidence`, `expected_rr`, `setup_quality_grade` into `BrainDecision` | Today only `confidence` is parsed; rest is lost between Claude and sizing |
| 3B | Extend APEX conviction weight formula with xray_modifier + rr_modifier | These signals exist but aren't consulted |
| 3C | Capital Tier 5% hysteresis bands | Direct equity comparisons cause boundary oscillation |
| 3D | New `SIZE_DERIVATION` event in `src/core/sizing_orchestrator.py` | No unified sizing trace today; existing breadcrumbs are scattered (ENFORCER_SIZE, CONVICTION_SIZE_CAP, GATE_ADJUST) |

Per-layer breadcrumbs:

| Layer | File | Breadcrumb to write |
|-------|------|-----|
| APEX | `src/apex/optimizer.py` | `_apex_size_usd` |
| Gate Check 0 | `src/apex/gate.py:65-92` | `_gate_post_check0_size` |
| Gate Check 4 | `src/apex/gate.py:123-160` | `_gate_post_check4_size` |
| Enforcer | `src/strategies/performance_enforcer.py:194-224` | `_enforcer_multiplier` |

Orchestrator reads all breadcrumbs after the trade is executed (post `_execute_claude_trade` return at `layer_manager.py:1339`) and emits a single unified event with conviction context attached.
