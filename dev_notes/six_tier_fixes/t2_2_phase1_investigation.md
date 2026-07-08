# T2-2 Phase 1 — F14 Zero-conviction trades investigation

## 1. Defect statement

Trades reach `_execute_claude_trade` with `xray_conf=0.00`, `setup_score=0.0`, and `expected_rr=0.00`. APEX sizes them DOWN (e.g. SOLUSDT today: $12000 -> $180) but the gate does not reject. The trade proceeds with $180 at lev=2. No structural backing, no setup confluence, no positive risk/reward — the trade exists only because Claude requested it.

Today's baseline: 1 such event in workers.log (matches the report's SOLUSDT 13:40:48 incident).

## 2. Conviction signals today

`src/apex/gate.py:140-194` (CHECK 4 CONVICTION_WEIGHT) reads three signals from the trade dict (stamped by layer_manager from the CoinPackage):

- `_xray_confidence`: 0.85+/0.70+/>0/==0 -> 1.20x / baseline / 0.85x / neutral
- `_setup_score`: 80+/68+/56+/>0 -> 1.20x / baseline / 0.90x / 0.80x
- `_expected_rr`: 3.0+/1.5+/>0 -> 1.15x / baseline / 0.90x

When all three are 0 the existing code leaves weight neutral (1.0) and sizing falls back to the per-capital ceiling. The trade IS sized down by the conviction-weighted capital cap (Phase 3B raised this ceiling to 0.5 of available capital) but never rejected.

## 3. Root cause

The CONVICTION_WEIGHT block was designed as a sizing knob (more conviction -> more size) without a reject floor. The all-zero case is "no package data" today; the gate treats it as "neutral" rather than "no basis for trade." The report cited this as F14.

## 4. Fix scope

Pre-CHECK precondition: when all three conviction signals are zero (or below operator-configurable thresholds), set `trade["_gate_rejected"] = "zero_conviction"` BEFORE the existing weight computation. layer_manager's new skip path (introduced in T2-1) handles the rest.

Aggressive-exploitation guard: defaults must reject only the all-zero case. Operator can tighten thresholds in config.toml later. NEVER blanket-reject below an aggressive default.

## 5. Configurable thresholds

New settings (defaults in `config.toml` `[gate]` or similar):

- `min_xray_conf_for_trade: float = 0.0` — trade rejected if `_xray_confidence < this`.
- `min_setup_score_for_trade: float = 0.0` — trade rejected if `_setup_score < this`.
- `min_expected_rr_for_trade: float = 0.0` — trade rejected if `_expected_rr < this`.

Combinator: reject when ALL three signals are AT-OR-BELOW their thresholds simultaneously. With defaults all 0.0 this means "reject only when xray_conf==0 AND setup_score==0 AND expected_rr==0" — i.e. exactly the SOLUSDT case. Operator-tightening one threshold above 0 narrows the gate without enforcing a multi-signal rule.

## 6. Investigation conclusions

1. Today's CHECK 4 sizes zero-conviction trades down but does not reject.
2. Fix uses the `_gate_rejected` flag introduced in T2-1.
3. Defaults preserve aggressive-exploitation: reject only the all-zero "no basis at all" case.
4. Operator-configurable thresholds via three settings keys.

Phase 2 proposal follows.
