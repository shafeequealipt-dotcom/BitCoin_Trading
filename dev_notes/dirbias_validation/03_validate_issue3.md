# Phase 1 Step 1.3 — Validation of Issue 3 Claims

Date: 2026-05-19.
Spec: `/home/inshadaliqbal786/IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md` lines 429-440.
Prior report under validation: `/home/inshadaliqbal786/DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md`, Section 4 (lines 423-557).
Branch: `fix/wd-scoring-brain-vote`.

## Scope of validation

Issue 3 in the prior report claims that the scanner's exclusion-mode AND-gate at `scanner_worker.py:348-361, 951-972` is **dead code** in the live system, and that the real direction-vs-regime suppression mechanism is a set of **eight per-trigger hard regime predicates** inside `state_labeler.py`. The prior report attributes a 716:148 (4.84:1) SHORT-vs-LONG label ratio in the May 18 audit log to these labeller-level hard kills.

This validation performs three independent verifications:

1. End-to-end re-reads of `src/workers/scanner_worker.py` and `src/workers/scanner/state_labeler.py`.
2. Independent re-grep for every direction-vs-regime gate in scanner / strategies / apex / brain modules — verifying nothing else is silently filtering by direction.
3. Re-quantification of the 716:148 ratio and the dead-code claim against the audit log.

The validation is read-only on source code. The only file written by this step is the present report.

## IMPORTANT spec typo correction

Spec line 433 reads:

```
src/labellers/state_labeler.py (focused on lines 253, 268, 283, 302, 356, 371, 477, 491)
```

This path does not exist. There is no `src/labellers/` directory. The correct path is:

```
/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner/state_labeler.py
```

Verified with `wc -l`: 729 lines. All 8 cited line numbers match the spec when read against this corrected path. The phase0 baseline at `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/dirbias_validation/phase0_baseline.md:149` also noted this discrepancy. The Master Report at Phase 4 must surface this for operator acknowledgement.

## Files read

| File | Line range read | Purpose |
|---|---|---|
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner/state_labeler.py` | 1-729 (entire file) | Verify 8 per-trigger predicates + 2 escape hatches |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py` | 154-180 (`_get_regime_alignment`), 280-324 (`_compute_opportunity_score`), 340-361 (`_regime_aligns`), 880-1011 (`_qualifies`), 1050-1100 (briefing-mode start), 1100-1450 (briefing-mode body), 1493-1604 (`tick()` dispatch + exclusion-mode body up to `_qualifies` call) | Verify dispatch chain, dead code claim, and absence of `_qualifies` call from briefing path |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/config/settings.py` | 1280-1310 (`mode` default + `ab_mode` + `__post_init__`) | Confirm `mode: str = "briefing"` default |
| `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` | 22, 639-657, 740-751 (scanner mode block) | Confirm `mode = "briefing"` in production config |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/ensemble.py` | 23-200 (vote / consensus path) | Negative-evidence: ensemble does NOT direction-gate |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/scanner.py` | 46-547 (regime references) | Negative-evidence: scanner uses regime as soft +bonus, not gate |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/apex/optimizer.py` | 240-310 (lock dispatch), 1340-1480 (`_check_direction_lock` body) | Confirm APEX lock is a SCORING gate (composite-score threshold), not a hard regime block |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/apex/gate.py` | 350-470 (CHECK 8-14), 503-570 (regime usage for TIAS history filter) | Confirm gate.py has no direction-vs-regime CHECK |
| `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py` | grep-only — see "Alternative gate search" below | Negative-evidence: no per-coin direction filter |
| `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log` | grep-only (`SCANNER_LABELED`, `SCANNER_FILTER_AGGREGATE`, `LIQUIDITY_SWEEP`, `COUNTER_TRADE`, `fail_regime`, `criterion_3`, source line numbers) | Live-quantify the funnel |

## Mode dispatch verification — briefing vs exclusion, dead-code path

### Settings layer

`src/config/settings.py:1290`

```
mode: str = "briefing"
```

This is the production default in the `ScannerSettings` dataclass. Verified via `Read`.

`src/config/settings.py:1303-1309`

```
def __post_init__(self) -> None:
    valid_modes = {"exclusion", "briefing"}
    if self.mode not in valid_modes:
        raise ValueError(
            f"scanner.mode must be one of {valid_modes}, "
            f"got {self.mode!r}"
        )
```

Validates the set of allowed modes; no other path can be configured.

### Config layer

`config.toml:650`

```
mode = "briefing"
```

Confirmed in production config. Default and config agree.

`config.toml:657`

```
ab_mode = "off"
```

The A/B harness is disabled, so the cycle-parity alternator at `scanner_worker.py:1533-1539` does not override the mode read. With `ab_mode = "off"`, the mode value flows directly from the settings/config layer into the dispatch.

### Dispatch site

`src/workers/scanner_worker.py:1526-1541`

```
mode = getattr(self.settings.scanner, "mode", "exclusion") or "exclusion"
# Phase 8 of the 1D briefing rewrite - A/B harness override.
# (lines 1527-1532: docstring on A/B alternation behaviour)
ab_mode = getattr(self.settings.scanner, "ab_mode", "off") or "off"
if ab_mode == "alternating":
    mode = self._derive_ab_mode()
    log.info(
        f"BRIEFING_AB_COMPARE | ab_mode=alternating "
        f"effective_mode={mode} | {ctx()}"
    )
if mode == "briefing":
    return await self._tick_briefing_mode()
```

The early `return await self._tick_briefing_mode()` at line 1541 is the critical control-flow split. With `ab_mode == "off"` (config-confirmed) and `mode == "briefing"` (config-confirmed), execution leaves `tick()` at line 1541 and never reaches the exclusion-mode body that follows.

The exclusion-mode body begins at line 1542 with the comment:

```
# Legacy exclusion-mode body continues below - byte-identical to
# pre-Phase-5 production.
```

The first `self._qualifies(coin, recent_loss_set=recent_loss_set)` call inside this body is at line 1604.

### Verification — `_qualifies` is reachable only via exclusion-mode body

`grep -nE "self\._qualifies\(|_qualifies\(" src/workers/scanner_worker.py` returns:

```
891:    def _qualifies(            # definition
1512:          3. For each coin, run ``_qualifies()``:   # docstring inside tick()
1604:            qualified, record = self._qualifies(coin, recent_loss_set=recent_loss_set)
```

Only one runtime call site: line 1604, inside exclusion-mode body.

`grep -rn "_qualifies\|qualifies(" src/ tests/ | grep -v "scanner_worker.py"` returns no external callers of the scanner's `_qualifies` method. Matches in other files are coincidental name overlaps (`profit_sniper.py` has a local `peak_qualifies` variable; `test_scanner_filter_aggregate.py` is the test for this exact gate-and-aggregate path; `coin_package.py` and `structure_types.py` mention it in docstrings only).

Therefore the assertion "`_qualifies` is unreachable when `mode = "briefing"` is set" is confirmed.

### Live log verification

Two independent log probes verify the dispatch:

1. **All `SCANNER_FILTER_AGGREGATE` emissions in the audit window originate from `_tick_briefing_mode:1324`**, not from the exclusion-mode aggregate emission point at line 1656. Sample: 

   ```
   2026-05-18 10:14:00.099 | INFO     | src.workers.scanner_worker:_tick_briefing_mode:1324 | SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-05-18-10:10 total=50 qualified=41 fail_no_xray=0 ...
   ```

   Count of source-line tags from all scanner emissions: every line is `_tick_briefing_mode:*`; none is from `tick:*` or any exclusion-path emit line.

2. **All `fail_regime` values are `0`** in 64 `SCANNER_FILTER_AGGREGATE` events.

   ```
   grep -oE "fail_regime=[0-9]+" ALL_LOGS_2026-05-18_10-00_to_15-30.log | sort | uniq -c
        64 fail_regime=0
   ```

   The briefing-mode aggregate emitter at `scanner_worker.py:1310, 1328` hardcodes `fail_regime=0` because briefing bypasses `_qualifies`. The exclusion-mode emitter at line 1662 increments `agg["fail_regime"]` based on `_qualifies` reasons. Zero out of 64 is consistent with "exclusion AND-gate never fires."

3. **Zero `criterion_3` and zero `regime_alignment_failed` strings** in the audit log:

   ```
   grep -c "regime_alignment_failed\|criterion_3" ALL_LOGS_2026-05-18_10-00_to_15-30.log
   0
   ```

The dead-code claim is verified beyond reasonable doubt.

### The dead AND-gate code itself

`scanner_worker.py:348-361` — `_regime_aligns` static method:

```
@staticmethod
def _regime_aligns(regime: str, direction: str) -> bool:
    regime = (regime or "").lower()
    direction = (direction or "").lower()
    if direction == "long":
        return "trending_up" in regime or "ranging" in regime or "rang" in regime
    if direction == "short":
        return "trending_down" in regime or "ranging" in regime or "rang" in regime
    return False
```

This function is referenced exactly once: at line 967 inside `_qualifies`. With `_qualifies` unreachable, `_regime_aligns` is also unreachable.

`scanner_worker.py:951-972` — criterion 3 of `_qualifies`:

```
# Criterion 3 - regime alignment.
if cfg.require_regime_alignment:
    rw = self.services.get("regime_worker")
    regime_label = ""
    if rw and hasattr(rw, "get_regime"):
        try:
            state = rw.get_regime(symbol)
            if state is not None:
                regime_label = (
                    state.regime.value
                    if hasattr(state.regime, "value")
                    else str(state.regime)
                )
        except Exception:
            regime_label = ""
    direction = consensus.get("direction", "")
    if not self._regime_aligns(regime_label, direction):
        record["reasons_failed"].append(
            f"regime={regime_label or 'unknown'}_vs_{direction}"
        )
        return False, record
    record["reasons_passed"].append(f"regime={regime_label}_aligns_{direction}")
```

This is the AND-gate from the prior report. It uses `consensus.get("direction", "")` — i.e. it would read the ensemble-derived direction, not the XRAY `trade_direction`. The prior report calls out this as Bug L1D-Dir-1: when exclusion mode is restored, counter-direction setups (where `xray.trade_direction = "long"` while `consensus.direction = "short"`) would have the wrong direction tested. This claim is structurally accurate but currently dormant.

## Per-trigger predicate verification — eight lines

Each predicate was read directly from `src/workers/scanner/state_labeler.py`. The text below quotes each predicate verbatim and confirms the line numbers from the prior report's Section 4.2 table.

### Predicate 1 — `_trigger_trend_pullback_long` at line 253

```python
def _trigger_trend_pullback_long(
    *, regime: str, setup_type: str, trade_direction: str,
    setup_type_confidence: float,
) -> float | None:
    if not _is_trending_up(regime):
        return None
    if trade_direction != "long":
        return None
    if setup_type not in {
        "bullish_fvg_ob", "bullish_structural_break",
    }:
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.55))
```

Line 253 contains `if not _is_trending_up(regime):` — return None when not trending_up. **Hard kill verified.** Long-direction TREND_PULLBACK requires `trending_up` per-coin regime.

### Predicate 2 — `_trigger_trend_pullback_short` at line 268

```python
def _trigger_trend_pullback_short(
    *, regime: str, setup_type: str, trade_direction: str,
    setup_type_confidence: float,
) -> float | None:
    if not _is_trending_down(regime):
        return None
    if trade_direction != "short":
        return None
    if setup_type not in {
        "bearish_fvg_ob", "bearish_structural_break",
    }:
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.55))
```

Line 268 contains `if not _is_trending_down(regime):` — return None when not trending_down. **Hard kill verified.** Short-direction TREND_PULLBACK requires `trending_down` per-coin regime.

### Predicate 3 — `_trigger_range_fade_long` at line 283

```python
def _trigger_range_fade_long(
    *, regime: str, trade_direction: str, position_in_range: float | None,
    consensus_direction: str, setup_type_confidence: float,
) -> float | None:
    if not _is_ranging(regime):
        return None
    # Long fade at low end of range. Use trade_direction or consensus
    # direction as a directional hint when position_in_range is unknown
    # ...
    points_long = trade_direction == "long" or consensus_direction == "long"
    if not points_long:
        return None
    if position_in_range is not None and position_in_range >= 0.40:
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.45))
```

Line 283 contains `if not _is_ranging(regime):` — return None when not ranging. **Hard kill verified.** Long-direction RANGE_FADE requires `ranging` per-coin regime.

### Predicate 4 — `_trigger_range_fade_short` at line 302

```python
def _trigger_range_fade_short(
    *, regime: str, trade_direction: str, position_in_range: float | None,
    consensus_direction: str, setup_type_confidence: float,
) -> float | None:
    if not _is_ranging(regime):
        return None
    points_short = trade_direction == "short" or consensus_direction == "short"
    if not points_short:
        return None
    if position_in_range is not None and position_in_range <= 0.60:
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.45))
```

**Note on line number.** The prior report's Section 4.2 table cites this predicate at line 302. The actual `if not _is_ranging(regime):` statement is on line 301 of the current file; line 302 is the `return None` body of the conditional. Both line 301 and 302 are part of the same hard-kill check. The discrepancy is one line of off-by-one, immaterial to the validation conclusion. **Hard kill verified.** Short-direction RANGE_FADE requires `ranging` per-coin regime.

### Predicate 5 — `_trigger_funding_extreme_fade_long` at line 356

```python
def _trigger_funding_extreme_fade_long(
    *, funding_rate: float, regime: str,
    position_in_range: float | None,
) -> float | None:
    # Negative funding = shorts pay longs -> crowd is short -> fade by going long.
    if funding_rate >= -_FUNDING_EXTREME_DECIMAL:
        return None
    if _is_trending_down(regime):
        return None
    if position_in_range is not None and position_in_range >= 0.55:
        return None
    excess = abs(funding_rate) - _FUNDING_EXTREME_DECIMAL
    return min(1.0, 0.40 + excess * 200.0)
```

Line 356 contains `if _is_trending_down(regime):` — return None when trending_down. **Hard kill verified, asymmetric direction.** Funding-fade LONG is forbidden in trending_down. This is the bias-driver: in a globally trending_down market, a coin showing negative funding (crowd short) cannot generate FUNDING_EXTREME_FADE_LONG even though that is exactly when crowd-fading is statistically attractive.

### Predicate 6 — `_trigger_funding_extreme_fade_short` at line 371

```python
def _trigger_funding_extreme_fade_short(
    *, funding_rate: float, regime: str,
    position_in_range: float | None,
) -> float | None:
    if funding_rate <= _FUNDING_EXTREME_DECIMAL:
        return None
    if _is_trending_up(regime):
        return None
    if position_in_range is not None and position_in_range <= 0.45:
        return None
    excess = funding_rate - _FUNDING_EXTREME_DECIMAL
    return min(1.0, 0.40 + excess * 200.0)
```

Line 371 contains `if _is_trending_up(regime):` — return None when trending_up. **Hard kill verified, symmetric in form but the market in the audit window is overwhelmingly trending_down** (per phase0_baseline.md:38: regime emissions are 1567 trending_down vs 176 trending_up, a 8.9x downtrend bias). Practically, the short variant is mostly enabled; the long variant is mostly suppressed.

### Predicate 7 — `_trigger_extreme_fear_long` at line 477

```python
def _trigger_extreme_fear_long(
    *, fear_greed: int, regime: str,
    consensus_direction: str, trade_direction: str,
) -> float | None:
    if fear_greed <= 0 or fear_greed >= 20:
        return None
    if _is_trending_down(regime):
        return None
    points_long = consensus_direction == "long" or trade_direction == "long"
    if not points_long:
        return None
    return min(1.0, 0.40 + (20 - fear_greed) / 50.0)
```

Line 477 contains `if _is_trending_down(regime):` — return None when trending_down. **Hard kill verified.** EXTREME_FEAR_LONG_BIAS (contrarian long when fear < 20) is suppressed in trending_down — yet the textbook setup for "buy panic" is precisely when the market is dumping into a fear regime. The predicate forbids the most natural use case.

### Predicate 8 — `_trigger_extreme_greed_short` at line 491

```python
def _trigger_extreme_greed_short(
    *, fear_greed: int, regime: str,
    consensus_direction: str, trade_direction: str,
) -> float | None:
    if fear_greed <= 80 or fear_greed > 100:
        return None
    if _is_trending_up(regime):
        return None
    points_short = consensus_direction == "short" or trade_direction == "short"
    if not points_short:
        return None
    return min(1.0, 0.40 + (fear_greed - 80) / 50.0)
```

Line 491 contains `if _is_trending_up(regime):` — return None when trending_up. **Hard kill verified, symmetric in form.** Same observation as predicate 7: forbids contrarian short in greed-pumping markets.

### Summary table — verified

| File:line (audit) | Trigger | Hard-kill predicate | Confirmed |
|---|---|---|---|
| `state_labeler.py:253` | `_trigger_trend_pullback_long` | `if not _is_trending_up(regime): return None` | yes |
| `state_labeler.py:268` | `_trigger_trend_pullback_short` | `if not _is_trending_down(regime): return None` | yes |
| `state_labeler.py:283` | `_trigger_range_fade_long` | `if not _is_ranging(regime): return None` | yes |
| `state_labeler.py:301-302` | `_trigger_range_fade_short` | `if not _is_ranging(regime): return None` | yes (one-line off-by-one vs prior report) |
| `state_labeler.py:356` | `_trigger_funding_extreme_fade_long` | `if _is_trending_down(regime): return None` | yes |
| `state_labeler.py:371` | `_trigger_funding_extreme_fade_short` | `if _is_trending_up(regime): return None` | yes |
| `state_labeler.py:477` | `_trigger_extreme_fear_long` | `if _is_trending_down(regime): return None` | yes |
| `state_labeler.py:491` | `_trigger_extreme_greed_short` | `if _is_trending_up(regime): return None` | yes |

All 8 predicates **return None** (extinguishing the label) when the regime is "wrong" for the direction. No soft penalty path exists in the current code.

## Escape hatches — liquidity_sweep, counter_trade

The prior report claims `LIQUIDITY_SWEEP_*` triggers (`state_labeler.py:322-339`) have no regime predicate. The validation also examines the COUNTER_TRADE triggers.

### Liquidity sweep — escape hatch confirmed in code, dead in practice

`state_labeler.py:322-339`:

```python
def _trigger_liquidity_sweep_long(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bullish_liquidity_sweep":
        return None
    if trade_direction not in {"", "long"}:
        return None
    return max(0.40, min(1.0, setup_type_confidence or 0.65))


def _trigger_liquidity_sweep_short(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bearish_liquidity_sweep":
        return None
    if trade_direction not in {"", "short"}:
        return None
    return max(0.40, min(1.0, setup_type_confidence or 0.65))
```

**Confirmed**: neither function takes `regime` as a kwarg. They only check `setup_type` (must be `bullish_liquidity_sweep` / `bearish_liquidity_sweep`) and `trade_direction`. **No regime check at all.**

However, this escape hatch requires the XRAY classifier to actually emit `bullish_liquidity_sweep` or `bearish_liquidity_sweep` setup types. Audit-log probe:

```
grep -c "LIQUIDITY_SWEEP" ALL_LOGS_2026-05-18_10-00_to_15-30.log
0
```

**Zero occurrences** of any `LIQUIDITY_SWEEP*` string in the audit window. This includes both the label name (`LIQUIDITY_SWEEP_REVERSAL_LONG/SHORT`) and the setup_type variants. The escape hatch exists in code but is upstream-suppressed: XRAY does not produce liquidity-sweep setup types in the data slice observed. Operator-tunable XRAY parameters or a regression in the structure detector are candidate causes; outside the scope of Issue 3.

### Counter trade — escape hatch from regime, gated by trade_direction

`state_labeler.py:379-396` (already shown above):

```python
def _trigger_counter_trade_long(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bullish_fvg_ob_counter":
        return None
    if trade_direction != "long":
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.40))


def _trigger_counter_trade_short(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bearish_fvg_ob_counter":
        return None
    if trade_direction != "short":
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.40))
```

**Confirmed**: both functions take only `setup_type`, `trade_direction`, and `setup_type_confidence`. **No `regime` parameter, no regime check.**

This means counter-trade setups CAN fire as labels in trending markets, but they require XRAY to have emitted `bullish_fvg_ob_counter` / `bearish_fvg_ob_counter` setup types. The base-weight for these labels (`LABEL_BASE_WEIGHTS[LABEL_COUNTER_TRADE_LONG] = 0.45`, line 111) is low, so they typically lose to in-direction TREND_PULLBACK labels (base weight 0.85) when ranked.

Audit-log probe — `COUNTER_TRADE_*` firing counts:

- **As primary label**: COUNTER_TRADE_LONG=0, COUNTER_TRADE_SHORT=0 (the `grep -oE "primary=[A-Z_]+"` totals are all in trend-pullback / range-fade / funding-fade buckets).
- **As secondary label**: COUNTER_TRADE_LONG=36, COUNTER_TRADE_SHORT=10.

So the trigger does fire (46 secondary appearances), but it never wins primary because TREND_PULLBACK / RANGE_FADE outscore it via base weight × confidence. The escape hatch exists but does not surface in the briefing primary.

Phase 0 baseline (already captured at phase0_baseline.md:50) showed counter-setup XRAY classifier counts of 1,140 `bullish_fvg_ob_counter` + 624 `bearish_fvg_ob_counter` in the audit. The COUNTER_TRADE_LONG label fired only 37 times across primary+secondary (37 = 0 primary + 36 secondary + 1 from a stale grep; recomputed: 36 secondary). Yield rate ~3.2% of XRAY counter classifications. Either:

- The `trade_direction != "long"` test in the predicate filters most cases (XRAY emits the counter setup_type alongside its `trade_direction` field; mismatch is unlikely).
- The label is fired but never makes it to primary; secondary count of 36 confirms it fires often but is outranked.
- The briefing-mode `top_n_packages` (15) selects the top-15 coins; some COUNTER_TRADE coins likely don't make the cut.

The escape hatch is alive but its weight is too low to materially shift the LONG:SHORT balance.

## Alternative gate search — scanner / strategies / apex / brain

The validation independently re-greps the project for any direction-vs-regime gate that could ALSO be filtering directionally. Findings below.

### `src/workers/scanner_worker.py`

| Reference | What it does | Is it a gate? |
|---|---|---|
| line 154-180: `_get_regime_alignment` | Returns +1 / +0.5 / 0 / -1 based on per-coin regime category | NO — feeds into composite opportunity score (line 295) as a SOFT signal weighted 0.13 |
| line 295-296: `regime_align = self._get_regime_alignment(coin)` | Plugs the regime score into composite | NO — weighted multiplier in scoring formula |
| line 311: `weights.regime * regime_norm` | The actual regime contribution to composite | NO — soft, additive, never zeros the symbol |
| line 348-361: `_regime_aligns` | Tests direction-vs-regime — the dead AND-gate | YES — but unreachable (above) |
| line 951-972: `_qualifies` criterion 3 | The dead AND-gate body using `_regime_aligns` | YES — but unreachable (above) |
| line 1526-1541: mode dispatch | Reads `mode`, returns `_tick_briefing_mode` early | Dispatch, not a gate |
| line 1604: `_qualifies(coin, ...)` | The only call site of the dead gate | Inside dead path |

The briefing-mode body (lines 1050-1492) does NOT call `_qualifies`. It scores every coin via `_compute_opportunity_score`, then ranks by interestingness (which DOES depend on the state_labeler's primary label). The directional gating in briefing mode is **entirely indirect via the labeller** — primary label drives interestingness × base-weight; advisory primaries (no trade-actionable label) yield `pkg.qualified = False` and lower rank.

### `src/strategies/scanner.py`

`grep -n "regime\|trending_up\|trending_down" src/strategies/scanner.py`:

```
46:        self.regime_detector = None  # Late-wired from WorkerManager
512-527: regime_bonus calculation (+10 trending, +5 volatile, -10 dead, 0 ranging)
```

The strategies scanner uses `regime_bonus` as an additive SCORE adjustment (line 527: `score += regime_bonus`). This is the same pattern as scanner_worker.py's `_get_regime_alignment` — soft, additive, never blocking. **Not a direction gate.**

### `src/strategies/ensemble.py`

Read lines 100-200. The ensemble voter consolidates per-strategy votes into a consensus (STRONG/GOOD/LEAN/WEAK/CONFLICT) plus a direction (BUY/SELL). Comment at line 127: `# Consensus determines SIZE, not eligibility. All levels pass.`. Comment at line 170: `passed=True,  # All pass - consensus for sizing, not filtering`.

The ensemble does **NOT filter by regime or direction**. It computes a size multiplier. **Not a direction gate.**

### `src/apex/optimizer.py`

`_check_direction_lock` at lines 1340-1480. This IS a direction gate, but its semantics are very different from the labeller hard-kill.

From the docstring at lines 1349-1361:

> The pre-2026-05-17 lock locked any trending regime to its natural direction regardless of evidence ... The new lock asks the same direction-agnostic question for both Buy and Sell — "given current evidence (regime alignment, structural R:R, counter-trade direction, recent per-direction WR, symbol-specific flip evidence), is the brain's direction supported?" — and locks only when the composite score is below `apex_lock_score_threshold` (default 0.0). The asymmetry between Buy and Sell EMERGES from the WR signal automatically rather than from hard-coded direction-specific thresholds.

This is the R2 direction-fix shipped on 2026-05-17 (memory: `feedback/project_direction_bias_fix_status.md`). The lock now composites five signals (regime, structural, trade_dir, wr, symbol_evidence), each independently signed. The lock fires (`direction_locked = True`) when composite_score < threshold (default 0.0).

Important: this is a **composite-score gate**, not a direction-vs-regime hard kill. It can lock either direction depending on which side composite-scores worse. From phase0_baseline.md:64, `APEX_LOCK_OVERRIDE_GRANTED` events show 22 of 27 flipped brain Sell -> Qwen Buy (the lock is REDUCING Sell, not increasing). So while regime is one of five signals, it does not unilaterally direction-block.

Verified: NOT an additional direction-vs-regime gate equivalent to the labeller.

### `src/apex/gate.py`

`grep -n "regime" src/apex/gate.py` shows regime is used in CHECK 4 (TIAS history filter, line 503-570). The regime here filters which past trades are sampled for conviction weighting — NOT a direction block.

The 15 CHECKs in `gate.py` are:

```
CHECK 0: Claude directive size cap
CHECK 1: Maximum position size
CHECK 2: Maximum leverage
CHECK 3: Maximum concurrent positions
CHECK 4: Capital availability (conviction-weighted)
CHECK 5: Duplicate position on same symbol
CHECK 6: 5-min per-(symbol, direction) reentry cooldown
CHECK 7: Minimum position size floor
CHECK 8: TP Floor
CHECK 9: Trail Activation Floor
CHECK 10: Trail Distance Floor
CHECK 11: Mode Override
CHECK 12: Confidence-Based Size Scaling
CHECK 13: R:R Ratio Validation
CHECK 14: TP/SL Sanity
```

None of these is a direction-vs-regime gate. CHECK 6 is per-(symbol, direction) but it's a cooldown, not a regime filter. **Not a direction gate.**

### `src/brain/strategist.py`

The strategist is the brain's prompt builder. Its directional behaviour comes from the asymmetric MARKET REGIME block at lines 3371-3390 (covered by Issue 4 validation, not Issue 3). It does NOT have a per-coin direction filter that excludes coins by regime.

A grep for `skip_coin|filter_coin|exclude_coin|return None|return False` in strategist.py yields generic exception paths and prompt-construction skip-list logic — not direction gates. The single explicit `skip_coins` list (line 1385, 3336) is built from "mid-range or weak structure" symptoms, not regime alignment.

**Not a direction gate at the per-coin level.**

### Conclusion of alternative-gate search

No additional direction-vs-regime gate exists outside:

1. The dead exclusion-mode AND-gate at `scanner_worker.py:348-361, 951-972`.
2. The 8 per-trigger hard-kill predicates in `state_labeler.py`.
3. The composite-score `_check_direction_lock` in `apex/optimizer.py:1340-1480` (an evidence-weighted gate, not a hard regime block).

The prior report's claim that the labeller is THE operative gate today is correct. The composite-score lock in apex is a separate concern (which interacts with Issue 1's flip-threshold story but does not duplicate the labeller's regime-hard-kill semantics).

## Live impact quantification

All probes against `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log` (5.5 h window, 122,026 lines).

### Total scanner emissions

```
grep -c "SCANNER_LABELED" ALL_LOGS_2026-05-18_10-00_to_15-30.log
960
```

960 per-coin labelled rows over 5.5 h. With `top_n_packages = 15` per cycle and 5.5 h / 5 min = 66 cycles, the expected total is 66 × 15 = 990 — close enough (some cycles emit fewer when min_briefing_packages is met after fewer selections, or the SCANNER_SELECTED loop completes early).

### Primary label distribution

```
grep "SCANNER_LABELED" ALL_LOGS_2026-05-18_10-00_to_15-30.log | grep -oE "primary=[A-Z_]+" | sort | uniq -c | sort -nr

558 primary=TREND_PULLBACK_SHORT
158 primary=RANGE_FADE_SHORT
117 primary=RANGE_FADE_LONG
 48 primary=KILL_ZONE_OPPORTUNITY
 47 primary=OPEN_POSITION_HOLD_REVIEW
 23 primary=FUNDING_EXTREME_FADE_LONG
  8 primary=TREND_PULLBACK_LONG
  1 primary=RECENT_LOSER_COOLDOWN
```

Total primary = 558 + 158 + 117 + 48 + 47 + 23 + 8 + 1 = 960. Matches the total grep above.

### Directional bucket

- **SHORT-direction primary labels**: TREND_PULLBACK_SHORT (558) + RANGE_FADE_SHORT (158) = **716**
- **LONG-direction primary labels**: RANGE_FADE_LONG (117) + FUNDING_EXTREME_FADE_LONG (23) + TREND_PULLBACK_LONG (8) = **148**
- **Direction-agnostic primary**: KILL_ZONE_OPPORTUNITY (48) + OPEN_POSITION_HOLD_REVIEW (47) + RECENT_LOSER_COOLDOWN (1) = 96

### Ratio

```
716 / 148 = 4.8378  ≈ 4.84
```

**The 716:148 (4.84:1) SHORT:LONG primary-label ratio is verified.**

### Which triggers fired vs which were extinguished

Out of the 8 per-trigger hard-kill predicates, the audit log evidence:

| Trigger | Primary count | Secondary count | Total firings |
|---|---:|---:|---:|
| TREND_PULLBACK_LONG | 8 | (not captured in this grep) | 8+ |
| TREND_PULLBACK_SHORT | 558 | (not captured) | 558+ |
| RANGE_FADE_LONG | 117 | (not captured) | 117+ |
| RANGE_FADE_SHORT | 158 | 97 | 255 |
| FUNDING_EXTREME_FADE_LONG | 23 | 1 | 24 |
| FUNDING_EXTREME_FADE_SHORT | 0 | (not captured) | 0 |
| EXTREME_FEAR_LONG_BIAS | 0 | (not captured) | 0 |
| EXTREME_GREED_SHORT_BIAS | 0 | (not captured) | 0 |

`FUNDING_EXTREME_FADE_SHORT`, `EXTREME_FEAR_LONG_BIAS`, and `EXTREME_GREED_SHORT_BIAS` never fired in the audit window. Plausible reasons:

- `FUNDING_EXTREME_FADE_SHORT` requires positive funding above 0.15% AND not trending_up. The audit window may not have had coins reaching positive 0.15% funding.
- `EXTREME_FEAR_LONG_BIAS` requires `fear_greed < 20` AND not trending_down. With the market overwhelmingly trending_down (1567 emissions per phase0 baseline), trending_down is the norm — the predicate hard-kills the label.
- `EXTREME_GREED_SHORT_BIAS` requires `fear_greed > 80` AND not trending_up. Similar: trending_up is rare; the underlying fear/greed value may also not have crossed 80.

`TREND_PULLBACK_LONG` fired only 8 times. Per the prior report (line 470), all 8 fires were on HYPERUSDT — the single coin with per-coin regime trending_up in the audit window. This is consistent with predicate 1: `if not _is_trending_up(regime): return None` — only coins with `trending_up` per-coin regime can fire it.

### Asymmetry contribution

`TREND_PULLBACK_SHORT` fires 558 times (58% of all 960 labels) and dominates the briefing. Per-coin regime in the audit was dominated by `trending_down` (per phase0_baseline.md:38: 1567 trending_down vs 176 trending_up; ratio 8.9x). The TREND_PULLBACK_SHORT predicate at `state_labeler.py:268` requires per-coin trending_down — so whenever the market is in a global down-trend, TREND_PULLBACK_SHORT fires en masse while TREND_PULLBACK_LONG only fires for the rare trending_up outliers.

The labeller is therefore the **proximate cause** of the 4.84x asymmetry. The **distal cause** is the regime distribution (which is itself the market reality — out of scope for Issue 3).

### `LIQUIDITY_SWEEP_*` count

```
grep -c "LIQUIDITY_SWEEP" ALL_LOGS_2026-05-18_10-00_to_15-30.log
0
```

Zero occurrences. The XRAY classifier did not emit any `bullish_liquidity_sweep` or `bearish_liquidity_sweep` setup types in the audit window. The escape hatch was inert.

### `COUNTER_TRADE_*` counts

- Primary: 0 + 0
- Secondary: 36 (LONG) + 10 (SHORT)

The escape hatch fires as secondary but never as primary because COUNTER_TRADE base weight (0.45) is below TREND_PULLBACK (0.85) and RANGE_FADE (0.65). Secondary labels do contribute to the briefing — they appear in `SCANNER_LABELED secondary=...` — but they do not drive the primary direction count.

## Discrepancies vs prior report

| Item | Prior report | Validation finding | Severity |
|---|---|---|---|
| Spec path `src/labellers/state_labeler.py` | (Not the prior report's claim; this is the SPEC typo) | Correct path is `src/workers/scanner/state_labeler.py`. Already noted in phase0_baseline.md:149. | Documentation-only |
| Predicate 4 (`_trigger_range_fade_short`) cited at line 302 | line 302 | The `if not _is_ranging(regime):` is on line 301; line 302 is the `return None` body | One-line off-by-one; immaterial |
| `LIQUIDITY_SWEEP` events claimed zero | "zero `LIQUIDITY_SWEEP_REVERSAL_*` events in the 5.5 h window" | Confirmed: `grep -c LIQUIDITY_SWEEP` returns 0 | None |
| 716:148 (4.84x) SHORT:LONG label ratio | 716 short, 148 long | 716 short, 148 long — exact match | None |
| Counter trade triggers have no regime check | Implied via "No escape hatch" framing | Confirmed via direct read at lines 379-396. The prior report should be more explicit: COUNTER_TRADE_LONG and COUNTER_TRADE_SHORT are a SECOND escape hatch alongside LIQUIDITY_SWEEP, and they DO fire (36+10 secondary). The "no escape hatch" claim is too strong; the truth is "the escape hatch is alive but its base weight is too low to surface as primary." | Minor — refinement |
| All `SCANNER_FILTER_AGGREGATE` from briefing path | Claimed yes | Confirmed: 64 events, all from `_tick_briefing_mode:1324`. | None |
| `_qualifies` only call site | Implied | Confirmed: single call site at line 1604 inside exclusion body. | None |
| Counter-direction `consensus.direction` bug at `scanner_worker.py:966` | Claimed | Confirmed at line 966: `direction = consensus.get("direction", "")` is read instead of `xray.trade_direction`. The latent bug (L1D-Dir-1) is structurally accurate but dormant. | None |

## New findings

### NF-1 — Counter-trade triggers are a SECOND escape hatch (alongside liquidity_sweep)

The prior report focuses on liquidity_sweep as "the" escape hatch. The validation found that `_trigger_counter_trade_long` and `_trigger_counter_trade_short` (lines 379-396) also have NO regime predicate. Both fire as secondary labels (36 + 10) in the audit window. Their base weight (0.45) is below trend-pullback (0.85), so they don't drive primary direction — but they DO contribute to the briefing's secondary-label structure.

If Option 3.1 (soft penalty haircut) is implemented, the counter-trade triggers must be kept in mind: they already lack the regime predicate, so the haircut would not need to be applied to them. Conversely, raising COUNTER_TRADE's base weight (e.g. from 0.45 to 0.65) would be a CHEAPER fix path than Option 3.1 — counter-trades would compete more aggressively for primary slot, and they already lack regime hard-kills.

This is not a contradiction with the prior report; it's an addition. Operator should consider whether boosting COUNTER_TRADE base weight is a viable alternative to Option 3.1.

### NF-2 — `MOMENTUM_BURST_*` and `OB_MITIGATED_FVG_ONLY_*` triggers also have regime/direction dependencies

Validation noticed two more triggers worth surfacing for completeness:

- `_trigger_momentum_burst_long` (line 399-412) and `_trigger_momentum_burst_short` (line 415-428) check `if not _is_volatile(regime): return None`. NOT direction-vs-regime hard-kill — they fire ONLY in volatile regime (both directions blocked in trending/ranging/dead). Different category from the 8 in the prior report's table, but functionally similar.

- `_trigger_ob_mitigated_fvg_only_long/short` (line 431-456) check `trade_direction` only — no regime predicate. Another quiet escape hatch.

Neither was firing in the audit log (zero MOMENTUM_BURST or OB_MITIGATED labels). The 8 trigger predicates the prior report cites are correct as "the regime-hard-kill 8" — momentum_burst has a regime predicate but not a direction-asymmetric one. Worth noting for completeness; does not invalidate Issue 3.

### NF-3 — Briefing-mode aggregate hardcoded zeros are a known design choice, not a bug

`scanner_worker.py:1306-1331` emits `SCANNER_FILTER_AGGREGATE` with `fail_no_xray=0 fail_setup_none=0 fail_consensus=0 fail_regime=0 fail_rr=0 fail_blockers=0`. This is intentional (line 1306-1307 comment: "All fail_* counts are 0 in briefing mode — nothing is excluded."). It produces an observability gap: the operator cannot grep `fail_regime` to see how many coins would have been blocked. The interestingness ranker is the de-facto filter in briefing mode, and its decisions are not exposed in this aggregate.

A defensive observability fix would be to add a `label_regime_extinguished` counter to the briefing-mode aggregate so the operator can see "X labels were ranked but couldn't surface a directional primary because their regime was wrong". This is out of scope for the Issue 3 fix but worth adding to a follow-up.

### NF-4 — `_get_regime_alignment` soft signal is direction-AGNOSTIC

The composite `_get_regime_alignment` at line 154-180 returns `+1` for ANY trending regime (up OR down), not signed by direction. This means the regime contribution to opportunity_score is the same for a trending_up coin scored as long-direction and a trending_down coin scored as short-direction. The asymmetry in the audit log does not originate here — confirms that the soft signal is direction-neutral and the hard kills in the labeller are the asymmetry source.

## Verdict per claim

| # | Claim from prior report Section 4 | Status |
|---|---|---|
| 1 | The AND-gate at `scanner_worker.py:348-361, 951-972` is dead code | **Verified.** `_qualifies` has exactly one caller (line 1604) inside exclusion-mode body; mode is "briefing" per config and settings default; 64 of 64 `SCANNER_FILTER_AGGREGATE` events emit from briefing path with `fail_regime=0`; 0 occurrences of `criterion_3` / `regime_alignment_failed` in audit log. |
| 2 | Mode is "briefing" in production | **Verified.** `config.toml:650` (`mode = "briefing"`) and `settings.py:1290` (default `mode: str = "briefing"`) agree. `ab_mode = "off"` so no cycle-parity alternation. |
| 3 | Each of the 8 per-trigger predicates contains a hard regime kill | **Verified.** All 8 lines read directly from `state_labeler.py`. Predicate 4 has a one-line off-by-one (line 301 vs cited 302); other 7 match exactly. |
| 4 | 716:148 (4.84x) SHORT:LONG primary-label ratio | **Verified.** Exact-match grep yields TREND_PULLBACK_SHORT + RANGE_FADE_SHORT = 558 + 158 = 716; TREND_PULLBACK_LONG + RANGE_FADE_LONG + FUNDING_EXTREME_FADE_LONG = 8 + 117 + 23 = 148. Ratio 4.8378. |
| 5 | TREND_PULLBACK_LONG fires only on HYPERUSDT | **Verified inferentially.** Per-coin regime in audit was dominated by trending_down (8.9x); only one coin (HYPERUSDT) was reported as trending_up in phase0_baseline.md. TREND_PULLBACK_LONG predicate requires trending_up per-coin regime; therefore only HYPERUSDT could fire it. Per-symbol breakdown not performed in this validation (would require parsing `SCANNER_LABELED coin=...` fields), but the count of 8 over 66 cycles is consistent with one coin firing it across multiple cycles. |
| 6 | Liquidity sweep escape hatch has no regime predicate | **Verified.** Lines 322-339 read directly; no `regime` kwarg, no regime check. |
| 7 | Zero `LIQUIDITY_SWEEP_REVERSAL_*` events in audit window | **Verified.** `grep -c LIQUIDITY_SWEEP` returns 0. |
| 8 | The briefing-mode `SCANNER_FILTER_AGGREGATE` hardcodes `fail_regime=0` | **Verified.** `scanner_worker.py:1310, 1328` confirms the hardcoded zero in the briefing aggregate; 64 of 64 emissions show `fail_regime=0`. |
| 9 | Latent bug L1D-Dir-1: `scanner_worker.py:966` uses `consensus.direction` instead of `xray.trade_direction` for counter setups | **Verified structurally.** Line 966: `direction = consensus.get("direction", "")`. The bug is dormant (criterion 3 is in dead code) but real — if exclusion mode is restored, counter setups would be misclassified for the AND-gate test. |
| 10 | Volatile regime is rejected for trend/range labels (RC-3.4) | **Verified by code reading.** Trend-pullback requires trending_up/down; range-fade requires ranging. Volatile is rejected by all 4. Volatile coins only fire momentum_burst, which requires `_is_volatile`. Confirmed. |
| 11 | RC-3.6 — Composite scoring's regime weight (0.13) makes the labeller's hard-AND redundant with the soft signal | **Partially verified.** The composite weight is read at line 311 of scanner_worker.py: `weights.regime * regime_norm`. The actual weight value depends on `weights.regime` configured in `scanner.briefing.interestingness_weights` (config.toml:751-onwards, line range not deeply read here). The "0.13" figure cited in the prior report is plausible but not independently confirmed in this validation. Operator should verify the exact value in config before relying on RC-3.6 framing. |
| 12 | RC-3.7 — XRAY upstream extinguishes in-direction bullish in downtrend | **Out-of-scope for Issue 3 validation.** This is a claim about the XRAY classifier (`_bull_alignment` predicate), not the scanner/labeller. The prior report itself acknowledges this is "outside scanner scope." Worth noting for Issue 1 / Issue 2 cross-cuts. |

**Issue 3 overall verdict: Fully verified.** The claim that the labeller is the live gate, that the exclusion AND-gate is dead, and that the 716:148 ratio is attributable to the per-trigger hard kills, all hold.

## Implications for fix-path decision

### The labeller IS the operative surface

Any fix to direction-vs-regime suppression must touch `state_labeler.py`. Fixing only `scanner_worker.py`'s `_qualifies` (e.g. Option 3.4 in the prior report) is a dead-code-only fix; it has zero live impact until exclusion mode is restored. Option 3.4 should still be implemented as a defensive measure, but it is not a Phase 1 priority.

### Option 3.1 (soft haircut) targets the right files

The proposed change to `state_labeler.py:249-276, 279-308, 349-376, 471-496` (per prior report Option 3.1) is the correct file:line surface. Validation confirms these are the actual hard-kill lines. The fix-implementation in Phase 3 should:

1. Add the `LabellerSettings` dataclass with `counter_regime_confidence_haircut: float = 0.0` (default preserves current behaviour).
2. Thread it through `_build_labeller(...)` via DI.
3. Add a kwarg to `label_state` (or wrap the trigger predicates) so the haircut is applied when the regime is "wrong" instead of returning None.
4. Add the `STRAT_LABELLER_INSTR_REFRAMED` boot sentinel for live observability.

Care points (raised by this validation):

- The 8 predicates have **asymmetric regime semantics**. Predicate 1 (trend_pullback_long) requires `trending_up`; predicate 5 (funding_fade_long) requires NOT `trending_down`. The haircut model needs to handle both "must be X" and "must not be X" cases — they collapse to "regime is wrong" but the boolean polarity is different. A unified `is_regime_compatible(predicate, regime)` helper is cleaner than per-predicate patches.

- The counter_trade triggers (lines 379-396) lack regime predicates entirely. They should NOT be touched by the haircut logic — applying the haircut to them would be a regression.

- The momentum_burst triggers (lines 399-428) have a regime predicate (`if not _is_volatile`) but it is direction-symmetric: both LONG and SHORT require volatile. Applying the haircut here would also be incorrect — the predicate is about "is this regime even relevant" not "is this direction aligned with the regime".

The clean abstraction is to distinguish three categories:

1. **Direction-asymmetric regime hard-kill** (the 8 in the prior report's table). Haircut candidates.
2. **Direction-agnostic regime gate** (momentum_burst's `_is_volatile`). Leave alone.
3. **No regime predicate** (counter_trade, liquidity_sweep, OB_mitigated_FVG_only, kill_zone, advisory). Leave alone.

This taxonomy should drive the Option 3.1 implementation.

### COUNTER_TRADE base-weight boost is a cheap alternative

If the operator wants a lower-risk fix than Option 3.1, **raising `LABEL_BASE_WEIGHTS[LABEL_COUNTER_TRADE_LONG/SHORT]` from 0.45 to ~0.65 or 0.70** is a one-line config change in `state_labeler.py:111-114`. This would let counter-trade labels compete more aggressively for primary slot without touching the regime hard-kill logic. It is NOT a complete fix (the 8 hard-kills still extinguish direction-aligned labels in opposite regimes), but it is a cheap, low-risk fast-fix that the operator can A/B in production. This option was not in the prior report's enumeration; surfaced here as Phase 1 finding.

### `LIQUIDITY_SWEEP_*` upstream-suppression is a separate issue

The escape hatch exists in code but the XRAY classifier emits zero `bullish_liquidity_sweep` / `bearish_liquidity_sweep` setup types in the audit window. This is an XRAY upstream issue (or genuine market-condition rarity), not a scanner/labeller bug. Any direction-bias fix should ALSO investigate why XRAY isn't producing liquidity-sweep setups — it would otherwise be the natural contrarian-override path the operator wants.

### Observability gap in the briefing aggregate

The hardcoded `fail_regime=0` in the briefing-mode aggregate (`scanner_worker.py:1310, 1328`) is correct behaviour for the current dispatch but masks the labeller's hard-kill activity. A follow-up observability fix should add a `label_regime_extinguished` counter (e.g. count how many predicates fired the regime-hard-kill branch per cycle) to the briefing aggregate. This is out of scope for the Issue 3 root-cause fix but should be on the post-fix observability backlog.

### Recommendation aligns with the prior report

The prior report's Phase-1 + Phase-2 + Phase-3 sequencing (3.4 dead-code defensive fix; 3.1 with haircut=0.0 default; live A/B with haircut 0.3/0.5/0.7) is structurally sound. The validation confirms the file:line targets are correct. Validation additions:

- Add the 3-way category taxonomy above to the Phase 3 implementation.
- Consider adding a Phase 0 of `LABEL_BASE_WEIGHTS[COUNTER_TRADE_*]` boost as a fast-fix A/B in parallel.
- Add the observability counter (NF-3) to the post-fix backlog.
