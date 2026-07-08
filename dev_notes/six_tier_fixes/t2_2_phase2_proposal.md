# T2-2 Phase 2 — F14 Zero-conviction trades proposal

## 1. Confirmed diagnosis

- `apex/gate.py` CHECK 4 (CONVICTION_WEIGHT) sizes zero-conviction trades DOWN but does not reject. Today's SOLUSDT trade at xray_conf=0.00 / setup_score=0.0 / expected_rr=0.00 reached execution (sized at $180 lev=2).
- `_gate_rejected` flag + layer_manager skip introduced in T2-1 are reusable for this fix.

## 2. Recommended solution

Add a precondition block at the top of CHECK 4 in `apex/gate.py`:

```python
# T2-2 / F14 zero-conviction reject. Defaults reject only when ALL
# three conviction signals are <= their thresholds (the all-zero case
# the report cited). Operator-tightenable via settings.gate.*.
_xray = float(trade.get("_xray_confidence", 0) or 0)
_setup = float(trade.get("_setup_score", 0) or 0)
_rr = float(trade.get("_expected_rr", 0) or 0)
_min_xray = float(getattr(self._settings, "min_xray_conf_for_trade", 0.0))
_min_setup = float(getattr(self._settings, "min_setup_score_for_trade", 0.0))
_min_rr = float(getattr(self._settings, "min_expected_rr_for_trade", 0.0))
if _xray <= _min_xray and _setup <= _min_setup and _rr <= _min_rr:
    reason = (
        f"zero_conviction xray={_xray:.2f}<={_min_xray:.2f} "
        f"setup={_setup:.1f}<={_min_setup:.1f} rr={_rr:.2f}<={_min_rr:.2f}"
    )
    trade["_gate_rejected"] = reason
    modifications.append(f"REJECTED:zero_conviction")
    log.warning(
        f"GATE_REJECT | layer=gate sym={symbol} reason=zero_conviction "
        f"xray_conf={_xray:.2f} setup_score={_setup:.1f} expected_rr={_rr:.2f} "
        f"| {ctx()}"
    )
    # Early return preserves trade dict shape; layer_manager skips on flag.
    return trade
```

## 3. Three options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A (recommended)** | Reject when all three signals are AT-OR-BELOW their (default-zero) thresholds. | Targets only the all-zero case by default. Operator-configurable. Preserves aggressive-exploitation. | Adds three new settings keys. |
| B | Reject when ANY signal is at-or-below threshold. | Stricter; rejects more trades. | Reduces trade frequency. Aggressive-exploitation impacted. |
| C | Hardcode all-zero reject; no settings. | Simplest. | Operator can't tighten without code change. |

## 4. Recommendation: A

Single operator decision: A / B / C, plus optional threshold overrides.

## 5. Aim preservation

- Default thresholds 0.0 / 0.0 / 0.0 means trades reject ONLY when ALL three signals are zero — the SOLUSDT "no basis whatsoever" case.
- Trades with even one positive signal proceed at the size determined by the existing CONVICTION_WEIGHT logic.
- Operator's aggressive-exploitation philosophy preserved unless they explicitly tighten.

## 6. Observability additions

- `GATE_REJECT layer=gate sym=X reason=zero_conviction xray_conf=... setup_score=... expected_rr=...` — WARN, fires when the reject engages.

## 7. Test plan (smoke, ≤10 min)

`tests/test_t2_2_zero_conviction_reject.py` — 4 tests (pure-math against a settings stub + minimal trade dict — but the gate needs `apex_gate` services injection, so I'll test the predicate function in isolation OR add it to the test that mocks the settings).

Given the gate's dependency on services + settings, I'll extract a small pure predicate function `_should_reject_zero_conviction(trade, settings) -> tuple[bool, str]` (or inline-document the math, mirroring the T1-2 pattern). Pure-math tests cover the predicate.

## 8. Operator decision required

A / B / C, and any threshold overrides.
