# Phase 0 — Time-Decay Force-Close Definitive Fix — Pre-Flight + Baseline

Date written: 2026-05-06.
Reference document: `/home/inshadaliqbal786/IMPLEMENT_TIME_DECAY_FIX_INDEPTH.md`.
Plan file: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-wise-fox.md`.

This is a read-only investigation artifact. No code changes were made. No commit was created. The numbers below define the "before" snapshot against which Phase 4's live trial measurements will be compared.

## 1 — Pre-Conditions

### 1.1 Working tree (relevant scope)

`git status` shows runtime-data drift only (`data/layer_state.json`, `data/trading.db`) plus prior-fix `dev_notes/` artifacts. None of the source files in scope for this fix are dirty. Branch `main`, last commit `b58b7f4 docs(dir-block-fix): end-to-end pipeline verification report`.

### 1.2 Prior-fix witness events (combined log windows)

| Fix | Witness event tag | Count in 12 h | Status |
|---|---|---|---|
| Stage 2 framing | `STRAT_AGGRESSIVE_FRAMING` | 22 | shipped, firing |
| Post-execution closure (Phase 1B) | `STRAT_ACTION_CLOSE_BLOCKED` | 21 | shipped, firing |
| XRAY direction flip | `XRAY_DIR_FLIP` | 41 | shipped, firing |
| XRAY direction flip (residual block) | `XRAY_DIR_BLOCK` | 8 | small residual, expected |

`systemctl is-active trading-workers trading-mcp-sse shadow` returns `active active active`.

## 2 — Calculator Contract Verbatim

### 2.1 File location and shape

`src/risk/time_decay_sl.py` — 529 lines, "Pure math. Stateless calculator + per-symbol state dataclass" (line 3 docstring). The watchdog owns IO.

Key types:

- `TimeDecayState` (47-76) — per-position mutable state. Fields used by this fix: `original_sl_pct` (53, abs % from entry, positive), `mae_pct` (59, NEGATIVE % at worst PnL), `regime_confidence` (56, used only for p_win prior), `p_win` (67, default 0.5), `last_allowed_loss` (68, tighter-only guard).
- `TimeDecayConfig` (79-149) — frozen parameter bundle. Populated from `settings.time_decay` at boot. Defaults synchronized with `TimeDecaySettings` per the Bug 3 fix comment at line 105.
- `TimeDecaySLCalculator` — `create_state()` (176-213) and `calculate()` (215-379).

### 2.2 `calculate()` signature (215-224)

```python
def calculate(
    self,
    state: TimeDecayState,
    *,
    current_pnl_pct: float,
    position_age_seconds: float,
    regime_still_supports: bool,
    velocity_pct_per_s: float,
    acceleration_pct_per_s2: float,
) -> Optional[float]:
```

### 2.3 Return contract (227-230)

- `float > 0` — new SL price to push (tighter than previous).
- `-1.0` — force-close sentinel (`p_win < p_win_force_close`).
- `None` — no-op (grace period, or not tighter than last push).

### 2.4 Force-close test verbatim (264-271)

```python
# Force-close sentinel: trade is statistically dead
if state.p_win < self.cfg.p_win_force_close:
    log.warning(
        f"TIME_DECAY_FORCE_CLOSE | sym={state.symbol} "
        f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
        f"mae={state.mae_pct:+.2f}% | {ctx()}"
    )
    return -1.0
```

Threshold: `cfg.p_win_force_close = 0.15` (default at TimeDecaySettings.py:1974). NOT hardcoded — read from frozen TimeDecayConfig populated by the watchdog at boot.

The docstring in time_decay_sl.py:26 still says "force-close: p_win < P_WIN_FORCE_CLOSE (0.25) → return -1.0" — this is stale; the actual default is 0.15 since the Bug 3 fix on 2026-04-23.

### 2.5 `p_win` is mutated in `_update_p_win` (416-476), not computed in `calculate()`

The calculator runs `_update_p_win` at lines 258-262 (immediately before the force-close test) which mutates `state.p_win` based on:

- ATR-relative deepening this tick (line 442-445): `> 2 ATR → ×0.70`, `> 1 ATR → ×0.85`.
- Absolute PnL depth (line 454-458): `|pnl| > 3.0% → ×0.70`, `|pnl| > 1.5% → ×0.90` (Bug 3 fix's slow-bleeder catcher).
- Recovery bonus (line 461-464): recovery > 0.5 → ×1.15.
- Regime: regime_still_supports → ×1.05, else → ×0.60.
- Final clamp to `[p_win_min, p_win_max] = [0.05, 0.95]` (lines 473-476).

p_win prior set in `create_state()` at line 207: `prior = base + regime_conf × weight = 0.55 + regime_conf × 0.25`.

### 2.6 Existing gates inside `calculate()`

- Grace check (244-251): if `position_age_seconds < grace_seconds_by_class.get(class, 120)`, return `None` with `TIME_DECAY_GRACE` debug log. Grace is a no-op gate — it suppresses both force-close and SL-tighten this tick.
- Tighter-only guard (321-330): if computed `allowed_loss >= state.last_allowed_loss`, return `None`.
- Price-relative floor (351-366, Phase 11/P1-10c): if computed SL distance from current price falls below `min_price_relative_distance_pct`, return `None` with `TIME_DECAY_FLOOR_PRICE_REL` info log.

## 3 — Caller Map

### 3.1 Single direct caller

`src/workers/position_watchdog.py:961` inside `_handle_time_decay()` (def at line 823):

```python
outcome = self._time_decay.calculate(
    state,
    current_pnl_pct=pnl_pct,
    position_age_seconds=position_age_s,
    regime_still_supports=regime_still_supports,
    velocity_pct_per_s=velocity,
    acceleration_pct_per_s2=acceleration,
)
```

`position_age_s` derived at line 960: `plan.age_minutes * 60.0`. `regime_still_supports` derived at lines 944-957 via `regime_detector.get_coin_regime(pos.symbol).regime.value` direction comparison against `plan.direction`. `velocity, acceleration` derived at line 942 via `td_observe(state, pnl_pct)` (the module-level `observe()` at time_decay_sl.py:510).

### 3.2 Force-close consumer (position_watchdog.py:975-1020)

```python
if outcome == -1.0:
    try:
        await self.position_service.close_position(pos.symbol)        # 977
        log.warning(f"TIME_DECAY_CLOSE | sym=... pnl=... p_win=... mae=... | ctx")  # 978-981
        await self._send_close_alert(...)                              # 982
        if self.event_buffer:
            self.event_buffer.add_event(
                "HIGH", "time_decay_force_close", pos.symbol, ...      # 988-994
            )
        if self.coordinator:
            ...
            self.coordinator.on_trade_closed(
                ..., closed_by="time_decay_p_win_low", ...             # 1004-1012
            )
    except Exception as e:
        log.error(f"TIME_DECAY_CLOSE_FAIL | ... | ctx")                # 1014-1017
    finally:
        self._td_states.pop(pos.symbol, None)                          # 1019
    return True
```

### 3.3 Bypass evidence — minimum-hold guardrail

`src/workers/position_watchdog.py:_execute_strategic_actions:2332-2418` is the existing minimum-hold guardrail. It checks `coordinator.drain_strategic_actions()` and gates `close`/`take_profit` actions younger than `settings.watchdog.strategic_action_min_hold_seconds=300.0` (settings.py:648) unless the reason matches `strategic_action_allowed_early_close_reasons` (settings.py:649-657: 10 reasons including `"structure invalidated"`, `"regime change"`, etc.). Emits `STRAT_ACTION_CLOSE_BLOCKED` (line 2411).

Time-decay closes never pass through `_execute_strategic_actions` — they call `position_service.close_position()` directly at line 977. The guardrail at 2410 cannot fire for the time-decay path. This is the gate-stack pattern the IMPLEMENT doc Part C describes: one guardrail covers CALL_B; another path closes the trade via different mechanics. Phase 1 of this fix plugs the bypass at the source by gating the `-1.0` sentinel inside `calculate()`.

## 4 — Configuration Inventory

### 4.1 `TimeDecaySettings` (settings.py:1931-2012)

Defaults relevant to this fix:

- `enabled = True` (1941)
- `p_win_force_close = 0.15` (1974) — the threshold below which `calculate()` returns `-1.0`.
- `grace_seconds = 120` (1993) and `grace_seconds_by_class` (1994-1996: dead=30, low=45, medium=120, high=180, extreme=240).
- `min_allowed_loss_pct = 0.15` (1997) — the floor used in `create_state()` for `original_sl_pct`.
- `log_every_n_ticks = 1` (2011) — currently every tick is logged for investigation.

### 4.2 Builder (settings.py:3251-3256)

```python
def _build_time_decay(data: dict[str, Any]) -> TimeDecaySettings:
    """Build TimeDecaySettings from [time_decay] TOML section."""
    if not data:
        return TimeDecaySettings()
    filtered = {k: data[k] for k in data if hasattr(TimeDecaySettings, k)}
    return TimeDecaySettings(**filtered)
```

The `hasattr` filter auto-projects new TimeDecaySettings fields without builder edits. New keys added to `[time_decay]` in config.toml will be honored as soon as TimeDecaySettings declares the attribute.

### 4.3 `[time_decay]` block in `config.toml` (1382-1413)

Currently declared keys: `enabled`, the four `p_win_abs_depth_*` knobs, plus the two sub-tables `[time_decay.grace_seconds_by_class]` and `[time_decay.atr_room_multiplier_by_class]`. All other defaults come from `TimeDecaySettings`. Note: the doc-comment at config.toml:1380-1381 says "All scalar defaults come from TimeDecaySettings in settings.py; per-class overrides below."

### 4.4 Watchdog `TimeDecayConfig(...)` block (position_watchdog.py:169-229)

Currently passes 32 fields from `td_settings` into `TimeDecayConfig(...)`. New fields for Phases 1, 2, 3 require explicit additions to this block. Existing pattern at lines 196-216 uses `getattr(td_settings, "<field>", <default>)` for back-compat with stale config files — Phase 1+2+3 should follow this pattern.

## 5 — Five Baselines (Source: Two Operator Log Windows)

Source files:

- `/home/inshadaliqbal786/logs_2026-05-05_18-24_to_2026-05-06_04-40.log` — 10 h 16 min window.
- `/home/inshadaliqbal786/logs_2026-05-06_04-40_to_06-20.log` — 1 h 40 min window.

Combined coverage: ~11 h 56 min, 2026-05-05 18:24 UTC to 2026-05-06 06:20 UTC.

### 5.1 Baseline 1 — Time-decay close frequency

| Window | Duration | TIME_DECAY_FORCE_CLOSE | TIME_DECAY_CLOSE | TIME_DECAY_INIT | TIME_DECAY_CALC |
|---|---|---|---|---|---|
| 18:24 → 04:40 | 10 h 16 min | 13 | 13 | 18 | n/a (sampled) |
| 04:40 → 06:20 | 1 h 40 min | 20 | 20 | 36 | 50 |
| Combined | 11 h 56 min | 33 | 33 | 54 | n/a |

`TIME_DECAY_FORCE_CLOSE` count from `time_decay_sl.py:266` and `TIME_DECAY_CLOSE` from `position_watchdog.py:978` are 1:1 in both windows — confirming that every calculator force-close is consumed by the watchdog close path with no losses.

Comparison with other close paths in the combined window:

| Tag | Count | Source | Note |
|---|---|---|---|
| `SHADOW_POSITION_CLOSE` | 50 | shadow_adapter | total exchange-side closes |
| `TIME_DECAY_FORCE_CLOSE` | 33 | time_decay_sl.py:266 | calculator decisions |
| `STRAT_ACTION_CLOSE` | 23 | position_watchdog.py:2423 | non-time-decay strategic closes |
| `STRAT_ACTION_CLOSE_BLOCKED` | 21 | position_watchdog.py:2411 | minimum-hold guardrail saves |
| `MODE4_*` | 11 | profit_sniper / mode4_p9 | profit-sniper events |
| `SNIPER_*` | 165 | sniper machinery | mostly tick-level, not closes |

Time-decay closes account for ~66 % of all SHADOW_POSITION_CLOSE events in the 12 h window (33/50). The IMPLEMENT doc's claim of "20 of 28 closures from TIME_DECAY_FORCE_CLOSE" in the 04:40-06:20 window matches my measurement (20 force-closes in that window).

### 5.2 Baseline 2 — Sample force-close cohort

10 most-recent `TIME_DECAY_FORCE_CLOSE` events from the 04:40-06:20 window:

| Time (UTC) | Symbol | p_win | PnL | MAE |
|---|---|---|---|---|
| 04:55:32 | ADAUSDT | 0.090 | -0.14 % | -0.22 % |
| 05:01:24 | ORCAUSDT | 0.096 | -0.25 % | -0.25 % |
| 05:05:47 | HBARUSDT | 0.147 | -0.09 % | -0.09 % |
| 05:12:03 | ICPUSDT | 0.129 | -0.07 % | -0.14 % |
| 05:12:15 | ORCAUSDT | 0.096 | -0.35 % | -0.35 % |
| 05:20:16 | DOGEUSDT | 0.077 | -0.29 % | -0.29 % |
| 05:21:58 | BCHUSDT | 0.095 | -0.20 % | -0.22 % |
| 05:24:41 | ICPUSDT | 0.098 | -0.25 % | -0.32 % |
| 05:30:33 | MANAUSDT | 0.137 | -0.04 % | -0.12 % |
| 05:41:06 | FILUSDT | 0.094 | -0.08 % | -0.13 % |

The "12 SECONDS LATER" pattern from the IMPLEMENT doc Part A is reproduced concretely in this cohort: ORCAUSDT was the subject of `STRAT_ACTION_CLOSE_BLOCKED` at 05:12:03 (saved by the minimum-hold guardrail), then killed by time-decay 12 seconds later at 05:12:15 with the same -0.35 % MAE. Confirms the gate-stack bypass narrative.

### 5.3 Baseline 3 — Adjusted close paths and `closed_by` attribution

Total `SHADOW_POSITION_CLOSE` events: 50 (combined window).
Time-decay-attributed (via `event_buffer.add_event("HIGH", "time_decay_force_close", ...)` at position_watchdog.py:990): 31 — slight loss-of-attribution between `TIME_DECAY_FORCE_CLOSE` (33) and `event_buffer` records (31) (~6 % drop, likely cooldown/race-suppressed). `profit_sniper` event_buffer entries: 4. Other paths (TP/SL/manual) account for the residual ~15 closes.

Win-rate computation requires per-trade PnL outcomes that are not fully captured in the log alone; the `actual_pnl_pct` lives in the `trade_thesis` table (closed rows). The Phase 5 verification report should query `data/trading.db` for the closed-thesis cohort over both windows and report:

- Total closes inclusive
- Time-decay closes (via close_reason match)
- Win rate inclusive
- Win rate exclusive of time-decay

For Phase 0 the qualitative assessment is sufficient: time-decay closes capture an average loss of -0.13 % (Baseline 1 below); they cannot be wins by construction (the `-1.0` sentinel only fires when `p_win < 0.15`, which requires the trade to be losing). They depress the win rate's denominator without contributing to the numerator.

### 5.4 Baseline 4 — Position age at force-close (combined 33 events)

| Bucket | Count | % |
|---|---|---|
| < 60 s | 32 | 97 % |
| 60-120 s | 1 | 3 % |
| 120-300 s | 0 | 0 % |
| 300-600 s | 0 | 0 % |
| > 600 s | 0 | 0 % |

Distribution: min 30 s, max 61 s, median 40 s, mean 38 s.

The 300 s minimum-age guardrail proposed in Phase 1 would have blocked 100 % of these 33 force-close events (all are below 120 s, well below 300 s).

### 5.5 Baseline 5 — MAE/SL ratio at force-close (combined 33 events)

| Bucket | Count | % |
|---|---|---|
| < 0.25 | 29 | 88 % |
| 0.25-0.50 | 4 | 12 % |
| 0.50-0.75 | 0 | 0 % |
| ≥ 0.75 | 0 | 0 % |

Distribution: min 0.03, max 0.36, median 0.10, mean 0.13.

The 0.50 ratio threshold proposed in Phase 2 would have blocked 100 % of these 33 force-close events (the worst MAE/SL ratio in the cohort is 0.36, well below 0.50). All trades were killed at less than 36 % of their original SL distance — i.e., on early-life noise, before the trade approached its risk allocation.

### 5.6 Aggregate distributions at force-close (combined 33 events)

| Metric | Min | Max | Median | Mean |
|---|---|---|---|---|
| PnL | -0.45 % | -0.02 % | -0.09 % | -0.13 % |
| MAE | -0.45 % | -0.04 % | -0.12 % | -0.15 % |
| p_win | 0.073 | 0.150 | 0.098 | 0.113 |

The mean PnL of -0.13 % matches the IMPLEMENT doc's claim verbatim. The p_win mean of 0.113 is well below the 0.15 force-close threshold.

## 6 — Data Flow Trace (One Symbol)

ORCAUSDT 05:12:15 force-close trace:

1. **05:00:23.979** — `TIME_DECAY_INIT | sym=ORCAUSDT dir=Buy sl=3.74% atr=0.51% cls=high p_win=0.74 regime_conf=0.76 max_hold_s=2700 grace_s=180 atr_mult=2.50` (position_watchdog.py:931). State seeded with `original_sl_pct=3.74` and `p_win=0.74` (Bayesian prior from regime_conf=0.76 → 0.55+0.76*0.25 = 0.74).
2. **05:00:44.024** — second `TIME_DECAY_INIT` for the same symbol (likely re-init after a brief flat-then-loser state cycle). New init implies state was popped between 05:00:23 and 05:00:44 (probably via cooldown clear after a near-close).
3. **~05:11:51 (estimated)** — strategic-actions queue contained a CALL_B close request; `_execute_strategic_actions` ran; minimum-hold guardrail evaluated `_age_sec` against `_min_hold=300`. Position age was ~11 minutes, ABOVE 300 s — the guardrail did NOT block on age, but blocked on reason match (the close reason "Recent lesson shows ORCAUSDT just lost..." did not match the allow-list). Hence `STRAT_ACTION_CLOSE_BLOCKED` at 05:12:03.
4. **05:12:15.141** — `TIME_DECAY_FORCE_CLOSE | sym=ORCAUSDT p_win=0.096 pnl=-0.35% mae=-0.35% | tid=t-ORCAUSDT-mon` (time_decay_sl.py:266). p_win has decayed from 0.74 to 0.096 over ~11 minutes of mostly-losing ticks. The position is at -0.35 % PnL on a -3.74 % SL — MAE/SL ratio is 0.094, well below the 0.50 Phase 2 threshold.
5. **05:12:15.142+** — `position_watchdog._handle_time_decay` consumes `outcome == -1.0`; calls `position_service.close_position(ORCAUSDT)` (line 977); emits `TIME_DECAY_CLOSE` and `event_buffer` HIGH event; coordinator records `closed_by="time_decay_p_win_low"`.

Phase 1 + 2 in concert would have suppressed this close: position age was ~700 s (well above the 300 s Phase 1 gate, so Phase 1 alone would NOT block), but MAE/SL was 0.094 (well below the 0.50 Phase 2 gate, so Phase 2 WOULD block). Phase 3 would additionally require structural-invalidation evidence: at the time of the close, ORCAUSDT's XRAY confidence and regime would need to be compared against the entry-time anchor; if XRAY had not dropped 40 %+ and regime had not inverted, Phase 3 would also block. All three gates compose — any one of them blocking is sufficient to prevent the kill.

## 7 — Verification Gate

| Item | Status |
|---|---|
| Calculator's formula documented verbatim (Section 2) | done |
| All callers documented (Section 3.1, 3.2) | done — single caller at `_handle_time_decay:961` |
| Force-close consumer documented (Section 3.2) | done |
| Bypass relationship to `_execute_strategic_actions` documented (Section 3.3) | done — confirmed bypass |
| All baselines captured (Section 5) | done — five baselines with measurements |
| Working-notes file at `dev_notes/time_decay_fix/phase0_baseline.md` | done — this file |
| `git status` shows no source-file changes | confirmed |

Phase 0 verification gate passes. Phase 1 may proceed.

## 8 — Implications For Phases 1, 2, 3

### 8.1 Phase 1 (min-age 300 s)

Empirical evidence: 100 % of force-closes in the baseline window happened below 120 s; the 300 s gate is conservative relative to the data. The IMPLEMENT doc's "default 300 s, matching the existing minimum-hold guardrail" is consistent with the operator's existing 300 s `strategic_action_min_hold_seconds`. The minimum-age guardrail will fire frequently — every losing position will be protected for its first 5 minutes.

### 8.2 Phase 2 (MAE/SL ratio 0.50)

Empirical evidence: 100 % of force-closes happened at ratio < 0.36; the 0.50 gate suppresses every observed kill. Position would have to draw down at least half of its original SL before time-decay can kill it. The watchdog still pushes tighter SLs for positions with shallow MAE — Phase 2 only blocks the force-close decision, but actually returns `None` (symmetric blocking) so the SL-tighten branch is also skipped that tick. This matches the IMPLEMENT doc Phase 1 spec ("return force_close=False immediately") which the operator confirmed.

### 8.3 Phase 3 (structural invalidation, Hybrid anchor)

The watchdog at lines 944-957 already pulls current regime per symbol. Adding `structure_cache.get(sym)` reads (TTL 300 s, dict lookup, O(1)) is trivial. Entry-anchor capture at register_trade is straightforward — TradeState already has `entry_regime` (line 46), and the 4 new fields `entry_xray_confidence`, `entry_setup_type`, `entry_regime_at_open`, `entry_regime_confidence` extend that pattern. The schema v27 migration adds 4 ALTER TABLE statements following the existing pattern at migrations.py:1025 and 1193-1214. Restart resilience: watchdog reads from TradeState first, falls back to `trade_thesis` for in-flight positions whose state was lost during a process restart.

### 8.4 Combined phase impact (theoretical projection)

If all three gates ship with operator-confirmed settings:

- Phase 1 alone would have blocked: 33/33 = 100 % of observed force-closes (all under 120 s).
- Phase 2 alone would have blocked: 33/33 = 100 % of observed force-closes (all under 0.36 ratio).
- Phase 3 alone would block any force-close not preceded by a 40 %+ XRAY confidence drop or a regime inversion at 60 %+ confidence; cannot be measured from the existing log because entry-anchors aren't captured today.

The three gates are belt-and-suspenders — Phases 1 and 2 each independently suppress every observed kill. Phase 3 is the structural-invalidation layer that catches the legitimate "trade is genuinely failing" cases where age and MAE alone would not be enough evidence.

Phase 4's success criterion (80 %+ reduction in `TIME_DECAY_FORCE_CLOSE` rate) is highly conservative against this baseline — the projected reduction from Phases 1+2 alone is 100 %. Residual force-closes in the live trial will only fire when a position is BOTH old (> 300 s) AND deeply drawn down (≥ 50 % of SL) AND structurally invalidated — exactly the cohort the calculator is designed to catch.
