# BETA 03 — XRAY Override Threshold Logic

This document decodes the two thresholds that govern the post-APEX XRAY direction-flip decision in `src/workers/strategy_worker.py`. It documents what each threshold means, where they live, and how they interact with the asymmetric flip-confidence floors that govern APEX itself.

## The two thresholds — purpose and location

### `xray_dir_flip_threshold_ratio` (the 3× flip threshold)

- **Defined**: `src/config/settings.py:817` — `xray_dir_flip_threshold_ratio: float = 3.0`
- **Set in config.toml**: line 403 — `xray_dir_flip_threshold_ratio = 3.0`
- **Read at**: `src/workers/strategy_worker.py:1624-1630`
- **Purpose**: Threshold for when the strategy_worker decides to FLIP the chosen direction based on structural R:R mismatch. Ratio = `rr_opposite / rr_chosen`. If ratio > 3.0, the strategy_worker considers flipping.
- **When the lock is NOT set** (regime is ranging/dead/unknown, or volatile with strong opp TIAS): the flip happens at ratio > 3.0×.

### `xray_lock_override_ratio_threshold` (the 10× override threshold)

- **Defined**: `src/config/settings.py:831` — `xray_lock_override_ratio_threshold: float = 10.0`
- **Not set in config.toml** — defaults from the dataclass.
- **Read at**: `src/workers/strategy_worker.py:1671-1675`
- **Purpose**: When APEX has set the direction lock, the structural-RR flip is normally suppressed (the lock wins). The override threshold raises the bar: ratio must exceed `xray_lock_override_ratio_threshold` (10.0) before the lock can be overridden by structural evidence.
- **When the lock IS set**: flip happens only at ratio > 10.0×.

The thresholds compound. The 3.0× threshold is the boundary at which "structure says flip"; the 10.0× threshold is the additional bar to override the lock. The lock turns a 3.0× boundary into a 10.0× boundary.

## The dead zone: 3.0× to 9.99×

When APEX has locked direction (line 1648 `_apex_locked = bool(trade.get("_apex_locked"))`), the decision in strategy_worker.py:1676-1717 is:

```
_lock_override_active = (
    _apex_locked
    and _ratio > _flip_threshold           # ratio > 3.0
    and _lock_override_threshold > _flip_threshold   # 10.0 > 3.0 — must hold for override to be possible
    and _ratio > _lock_override_threshold  # ratio > 10.0
)
if _apex_locked and _ratio > _flip_threshold and not _lock_override_active:
    # emit XRAY_FLIP_SUPPRESSED_BY_LOCK
    ...
elif _lock_override_active:
    # emit XRAY_OVERRIDE_LOCK; fall through to flip logic
    ...
```

The dead zone for any locked trade is: `3.0 ≤ ratio < 10.0`. In this band:

- The 3.0× flip threshold IS cleared (so the strategy_worker considers the flip).
- The 10.0× override threshold is NOT cleared (so the override does not fire).
- Result: `XRAY_FLIP_SUPPRESSED_BY_LOCK` emitted and the trade proceeds in the locked direction.

This is the band where the BSBUSDT $70 loss happened (7.3× ratio).

The dead zone exists by design — when J3 introduced the override (commit 2120d22), the operator wanted the lock to remain dominant at moderate ratios and only get over-ruled at extreme ratios. The 10.0 default was deliberately conservative based on the audit-window distribution (4.9×, 17.6×, 30×, 324×, 338× — verified in settings.py:824-827 comment). The conservative pick is exactly what R3 calls out as too tight.

## Direction symmetry of the thresholds

Both thresholds are direction-agnostic. The ratio computation at strategy_worker.py:1618-1622:

```
_ratio = 0.0
if direction == "Buy" and _sp.rr_long > 0:
    _ratio = _sp.rr_short / _sp.rr_long
elif direction == "Sell" and _sp.rr_short > 0:
    _ratio = _sp.rr_long / _sp.rr_short
```

When chosen=Buy, `ratio = rr_short / rr_long` (how much better is Short than Long). When chosen=Sell, `ratio = rr_long / rr_short`. Both produce a ratio that, when high, means "the opposite direction has materially better R:R." A single threshold applies in both cases.

**The threshold is NOT direction-asymmetric in the current code.** A 7.3× ratio favoring Long (suppress Sell, flip to Buy) gets the same treatment as a 7.3× ratio favoring Short (suppress Buy, flip to Sell). Both fall into the dead zone, both emit XRAY_FLIP_SUPPRESSED_BY_LOCK, both produce a trade in the brain's chosen direction.

This is in tension with the asymmetric flip-confidence thresholds inside APEX (covered next), where Buy→Sell is harder (0.95) than Sell→Buy (0.70). The XRAY threshold has no equivalent asymmetry.

## Interaction with asymmetric flip-confidence thresholds

The APEX-side confidence gates live in `_resolve_flip_threshold()` at optimizer.py:1409-1449:

```
if claude_direction == "Buy" and qwen_direction == "Sell":
    return float(getattr(self._settings,
        "apex_min_flip_confidence_buy_to_sell", legacy))  # default 0.95
if claude_direction == "Sell" and qwen_direction == "Buy":
    return float(getattr(self._settings,
        "apex_min_flip_confidence_sell_to_buy", legacy))  # default 0.70
return legacy  # 0.70
```

Default values from config.toml:1529-1530:
- `apex_min_flip_confidence_buy_to_sell = 0.95`
- `apex_min_flip_confidence_sell_to_buy = 0.70`

This is the existing system's aim-bias awareness inside APEX. The reasoning (from the inline doc): Buys historically win more frequently and produce more profit; flipping Buy→Sell should be HARDER (require 0.95 confidence) because we are more often abandoning a winning direction. Flipping Sell→Buy should be EASIER (only 0.70 confidence required).

But this asymmetry ONLY APPLIES IN `_enforce_flip_confidence`, which runs only for `regime in ("ranging", "dead", "unknown")`. For trending/volatile regimes — which is where the lock fires — the asymmetric thresholds NEVER engage. The lock vetoes Qwen's flip BEFORE the confidence gate has a chance to run.

So the current system has:

| Regime | Flip mechanism | Symmetric? |
|---|---|---|
| trending_up/down | APEX lock + structural override at 10× | Symmetric (no asymmetric bias) |
| volatile | APEX lock + structural override at 10× | Symmetric |
| ranging/dead/unknown | Confidence gate at 0.70 (Sell→Buy) or 0.95 (Buy→Sell) | Asymmetric (Buy-favoring) |

The asymmetric flip-confidence already encodes the aim bias the operator wants. But it only applies to ranging/dead/unknown regimes — which is exactly the regimes the lock does NOT fire on. The asymmetric thresholds and the lock occupy disjoint regime sets; they never compose. The asymmetric levers are inert in trending/volatile sessions.

## Threshold tunability

Both thresholds are operator-tunable, but the discoverability is uneven:

- `xray_dir_flip_threshold_ratio = 3.0` — explicit in `config.toml` (line 403). Operator knows it is configurable.
- `xray_lock_override_ratio_threshold` — NOT in config.toml. The default 10.0 only lives in the dataclass at settings.py:831. An operator who has never seen the audit-window comment in settings.py would not realize this knob exists.

If the override threshold becomes a key tunable, it should be surfaced in config.toml with the same prominence as the flip threshold. The current state of "configurable in code but not in config.toml" is a discoverability gap that the R3 fix should resolve regardless of which option is chosen.

## How the threshold interacts with conviction history

The current threshold does NOT consider conviction. The CONVICTION_WEIGHT signal — computed in `apex/gate.py:_get_conviction_weight()` (per-coin profit factor, regime-filtered, 5-minute cache) — is used to scale POSITION SIZE, not to gate direction. The TIAS conviction is consumed at:

- `apex/gate.py:676-805` — `_get_conviction_weight()` returns a 0.5×-2.5× size multiplier.
- Used inside CHECK 4 (capital availability) at lines 184-237 — `weight = await self._get_conviction_weight(symbol)`.

The conviction value is computed per (symbol, regime) and is highly relevant to direction decisions — but the XRAY override path at strategy_worker.py:1671-1717 never reads it. A conviction-aware override threshold (one of the R3 options) would be a new code path.

## How the threshold interacts with aim-bias evidence

The aim-bias evidence — the 200-trade aggregate showing Buy 55.6 % WR vs Sell 41.8 % WR — is computed but never consumed by either threshold. The asymmetric `apex_min_flip_confidence_*` values encode this aim bias only for the unlocked regimes. The trending/volatile regimes that the lock dominates have no aim-bias awareness in any threshold today.

## Summary for synthesis

- The override threshold is a single, symmetric scalar (10.0). It is unaware of direction, conviction, regime confidence, and aim-bias evidence.
- The dead zone between 3.0× (flip considered) and 10.0× (override permitted) is where the May 16 suppressions (3.0-7.3×) all landed.
- Asymmetric flip-confidence thresholds exist inside APEX but ONLY apply to ranging/dead/unknown regimes — the regimes the lock does not fire on. The aim-bias asymmetry is structurally invisible to the lock's primary failure mode.
- Tunability: `xray_dir_flip_threshold_ratio` is in config.toml; `xray_lock_override_ratio_threshold` is not. Surfacing the latter is a baseline action regardless of which R3 option is chosen.
- The threshold is the simplest knob in the system. Changing it from 10.0 to 3.0 collapses the dead zone entirely; changing it to 5.0 admits the BSBUSDT 7.3× case but holds the lock at the 3.0-4.9× band.
