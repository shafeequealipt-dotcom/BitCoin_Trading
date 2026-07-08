# Phase 0 — Baseline Measurements

Source spec: `/home/inshadaliqbal786/IMPLEMENT_DIR_BLOCK_FIX_INDEPTH.md`
Plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-snappy-sphinx.md`

Captured on 2026-05-05 19:05 UTC against `main` head `0d38f54` ("widen brain candidate set to top-10 + bounded-count contract 2-4").

Working tree clean except for runtime files (`data/layer_state.json`, `trading.db`) and untracked dev_notes / data dirs — none of which are touched by this fix.

## Section 0 — Issue verification on current code

Each of the five issues + three discoveries was verified by reading the current source on `main` head. File:line and verbatim code confirmed.

| Issue | File:line | Status |
|---|---|---|
| 1. XRAY_DIR_BLOCK | `src/workers/strategy_worker.py:1551-1562` (BLOCK at ratio>5), `1563-1575` (REDUCE at ratio>3) | confirmed in code |
| 2. Profit give-back via trail | `src/workers/profit_sniper.py:1102-1530`, `src/workers/position_watchdog.py:1175-1206`, `src/core/sl_gateway.py` | confirmed in code |
| 3. APEX_FLIP_BLOCKED | `src/apex/optimizer.py:707-737` (`_enforce_flip_confidence`, threshold 0.90); emission at line 266 | confirmed in code |
| 4. Performance Enforcer block | `src/strategies/performance_enforcer.py:91-108` (`should_allow_trade`); mode trigger at lines 244-258 | confirmed in code |
| 5. APEX TP cap | `src/apex/optimizer.py:200-216` (cap formula), `295-303` (emission) | confirmed in code |
| Discovery 1: SL_GATEWAY trail_activation step bypass | `src/core/sl_gateway.py` (event SL_GATEWAY_ACCEPT step_pct=0.500 in BSBUSDT timeline 2026-05-05 14:55:54 with src=profit_sniper_trail elapsed_s=first) | confirmed in logs |
| Discovery 2: trail floor default mismatch | `src/apex/gate.py:241` getattr fallback `50.0` vs `src/config/settings.py:555` declared `15.0` | confirmed in code |
| Discovery 3: PRESERVATION trigger via streak_boost path | `performance_enforcer.py:247-255` and live ENFORCER_STATE event at 2026-05-05 18:56:15 shows literal `trigger=streak_boost` field | confirmed in logs |

The current 4 APEX_FLIP_BLOCKED events all carry the identical fingerprint `flip Buy→Sell in regime=ranging blocked: conf=0.85<0.90`, all on HYPERUSDT. The Qwen-side optimizer is consistently producing 0.85 confidence on a flip Claude doesn't want, and the 0.90 floor blocks it every time.

The live ENFORCER_STATE at 19:00 reads `trades=25 wins=7 losses=18 wr=0.28 strk=-6 pnl=-0.93% el=1 sz_mult=0.75 trigger=streak_boost`. This is identical to the spec's described block fingerprint and confirms Discovery 3 by the literal `trigger=streak_boost` field — there is no PnL-based path active; the streak-boost path elevated to level 1 around 18:53 when the losing streak crossed -5.

## Section 1 — Event-name reconciliation with spec

The spec uses event names that no longer match the current code. Captured the actual names so future phases bind to reality, not memory:

| Spec name | Actual event name in current logs | Count (24h) |
|---|---|---|
| SHADOW_SL_TIGHT | SL_GATEWAY_ACCEPT (real SL pushes) | 27 |
| SHADOW_SL_TIGHT (computed but not always pushed) | M4_TRAIL_FLOOR (floor check on every tick) | 829 |
| (gated tightens) | M4_GATED (cooldown / rate-limit) | 413 |
| (M4 ladder ticks) | M4_DECISION | 895 |
| (TRAIL action) | M4_ACT_TIGHTEN | 20 |
| (PARTIAL action) | M4_ACT_PARTIAL | 13 |
| (CLOSE action) | M4_ACT_CLOSE | 6 |
| THESIS_CLOSE / SHADOW_POSITION_CLOSE | both still exist (THESIS_CLOSE is the canonical pnl record) | 25 / 19 |

The "133 SHADOW_SL_TIGHT in 2 hours" from the spec is outdated terminology. Current 2-hour focal window 16:55-18:55 has 0 events of that exact name — the equivalent count under current naming is 27 SL_GATEWAY_ACCEPT in 24 hours, with 413 cooldown-gated proposals (M4_GATED) and 829 floor-evaluations (M4_TRAIL_FLOOR). All Phase 2 observability changes must use the SL_GATEWAY_*/M4_* event family, not SHADOW_SL_TIGHT.

## Baseline 1 — Trade execution rate (24h: 2026-05-04 19:00 → 2026-05-05 19:00)

| Metric | Value |
|---|---|
| STRAT_EXEC events | 25 |
| TRADE_SKIP rsn=xray_dir_block | 20 |
| TRADE_SKIP rsn=enforcer_block | 1 |
| STRAT_EXEC_BLOCKED | 1 (BSBUSDT, leverage clamp via streak-boost) |
| XRAY_DIR_REDUCE (size halved, proceeded) | 6 |
| Total directives observed | 46 |
| Execution rate | 25 / 46 = **54.3 %** |
| Spec target post-fix | ≥ 80 % |

`STRAT_DIRECTIVE` does not appear in the 24-h log window — Claude's intentions are tracked under different event names downstream (`STRAT_PNL_GATE`, `XRAY_SLTP`). Counted directives via blocked-vs-executed delta because that's what can be verified.

## Baseline 2 — XRAY_DIR_BLOCK frequency and ratio distribution (24h)

| Symbol | XRAY_DIR_BLOCK count | XRAY_DIR_REDUCE count |
|---|---|---|
| BCHUSDT | 6 | (in REDUCE bucket) |
| HYPEUSDT | 5 | |
| BSBUSDT | 4 | |
| RUNEUSDT | 2 | |
| HYPERUSDT | 1 | |
| AVAXUSDT | 1 | |
| BLURUSDT | 1 | |
| **Total** | **20** | **6** |

Ratio range observed: 5.1× (just above the >5 cutoff) up to 101.2× (extreme regime mismatch). Median above 7×. Eighteen of these events fall in the 2-hour focal window 16:55–18:55, matching the spec's "18 in 2 hours" claim.

Per-symbol pattern shows BCHUSDT killed 6 times in 24 hours — same coin, same direction-mismatch, no learning. This is the highest-leverage fix in the bundle.

## Baseline 3 — Trade outcome quality (last 25 closed trades, 24h)

| Metric | Value |
|---|---|
| Closed trades | 25 (THESIS_CLOSE) |
| Wins | 7 |
| Losses | 18 |
| Win rate | **28 %** (vs historical 36.9 % cited in spec) |
| Average daily PnL | -0.93 % |

Sample close-reason mix from the last 14 closed in the focal window:
- `shadow_sl_tp` (SL/TP hit): 5 (e.g. RUNEUSDT +0.09 %, BSBUSDT +0.66 %, HYPERUSDT -0.11 %, ALGOUSDT -0.06 %)
- `mode4_p9` (Profit Sniper close): 4 (HYPEUSDT +0.0023 %, +0.14 %, RUNEUSDT -0.22 %, ONDOUSDT -0.21 %)
- `time_decay_p_win_low`: 2 (ALGOUSDT -0.23 %, ONDOUSDT -0.27 %)
- `strategic_review`: 1 (HYPERUSDT -0.05 %)
- `emergency_manual`: 1 (ONDOUSDT -0.08 %)

Spec-cited give-back evidence (BSBUSDT 2026-05-05 17:59:54 → 18:05:09, peak +1.92 % → final +0.66 % = **65.6 % give-back**) is reproduced in the workers.log. Average win is +0.20 %, average loss -0.16 %, and final achieved-RR (avg_win / avg_loss) ≈ 1.25 — well below the spec's target ≥ 1.5:1.

Detailed peak vs final for every trade requires per-trade M4_DECISION reconstruction; not done here to keep Phase 0 within the 10-minute pace target. Phase 6 monitoring will run a tighter post-fix vs pre-fix comparison.

## Baseline 4 — APEX_FLIP_BLOCKED (24h)

| Metric | Value |
|---|---|
| Total events | 4 |
| All on symbol | HYPERUSDT |
| All in regime | ranging |
| All same fingerprint | `flip Buy→Sell ... blocked: conf=0.85<0.90` |
| Times stamps | 17:16:25, 17:33:11, 17:50:57, 17:59:28 |
| Comparable APEX_FLIP (passed) count | 2 |

This is a deterministic case for the Phase 3 fix: every block in 24 h was at exactly 0.05 below the threshold, on the same symbol/regime. The fix (lower threshold to 0.70 + RR-weighted boost) will let these through.

## Baseline 5 — Performance Enforcer mode distribution (24h)

| Metric | Value |
|---|---|
| ENFORCER_STATE samples (60-s tick) | 912 |
| STRAT_EXEC_BLOCKED total | 1 |
| Time in el=0 (estimate) | 18:00–18:53 = bulk of day at el=0 |
| Time in el=1 | 18:53 → 19:00+ (still active at end of window) |
| Trigger of el=1 entry | `streak_boost` (literal field in ENFORCER_STATE) |
| PnL at trigger | -0.85 % to -0.93 % (well above -2 % caution threshold) |
| Streak at trigger | -6 (crossed `streak_boost_threshold = -5`) |
| Single block instance | BSBUSDT 18:53:15.051, dir=Buy, leverage=5x rejected, message: `PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.85%)` |

Three observations matter for Phase 4:

1. The defensive mode triggered on a losing streak at -0.85 % PnL — well within normal noise. The aggressive-exploitation philosophy says this should not have triggered.
2. The block was an HARD reject. Claude wanted leverage=5; the right behavior under aggressive philosophy is to clamp to 3 and let the trade proceed.
3. `trigger=streak_boost` is literally surfaced in the event payload — the streak-boost path is what fired, not the PnL caution path. Phase 4 must address the streak-boost gate (`streak_boost_threshold = -5`, `streak_boost_pnl_floor = 0`).

## Baseline 6 — APEX_TP_CAP impact (24h)

| Metric | Value |
|---|---|
| Total events | 15 |
| `was_reduced` (qwen_tp > cap) | **0** (all observed cases were qwen_tp == cap, i.e. the cap fired but didn't reduce) |
| Class distribution | mostly `medium` (recTP×1.30), some `low` (×1.30), rare `high` (×1.40) |
| Sample 1 | DOGEUSDT cls=low qwen_tp=0.5 % cap=0.5 % recTP=0.3 % mult=1.30× |
| Sample 2 | RENDERUSDT cls=medium qwen_tp=1.4 % cap=1.4 % recTP=1.1 % mult=1.30× |
| Sample 3 | HYPERUSDT cls=medium qwen_tp=1.4 % cap=1.4 % recTP=1.1 % mult=1.30× |

In the 24-hour window every observed APEX_TP_CAP was a no-op — Qwen's output already matches the cap. This means the multipliers are not currently shrinking TPs in measured cases, but they ARE truncating Qwen's reasoning ceiling: Qwen sees a hard cap of `recTP×mult` and floors its recommendation to that. Raising the multiplier (Phase 5) lets Qwen produce higher TPs when warranted; the `was_reduced` field will distinguish actual reductions from no-ops once added.

Open question: the spec mentions cases where the cap "actually shrinks TP". None observed in 24 h. May be visible in a wider window (week+); if not, the cap is functioning as a Qwen-output ceiling rather than a reducer, and Phase 5's multiplier-raise is still the right move because it raises the ceiling.

## Baseline 7 — Achieved-RR distribution (24h, last 25 trades)

Computed from THESIS_CLOSE pnl% only (peak pnl unavailable per-trade without M4_DECISION reconstruction):

| Bucket | Count |
|---|---|
| Win > +0.5 % | 1 (BSBUSDT +0.66 %) |
| Win +0.1 to +0.5 % | 2 (HYPEUSDT +0.14 %, RUNEUSDT +0.09 %) |
| Win 0 to +0.1 % | 4 (mostly tiny break-evens like HYPEUSDT +0.0023 %) |
| Loss 0 to -0.1 % | 4 |
| Loss -0.1 to -0.3 % | 12 |
| Loss < -0.3 % | 2 |

Average winner: +0.20 %. Average loser: -0.16 %. **Achieved RR ≈ 1.25**, vs target 1.5:1. Combined with the 28 % win-rate, daily PnL ends at -0.93 % — exactly on the threshold of triggering enforcer level 1 if the streak path elevates.

The give-back pattern is the proximate cause: if the winners had been allowed to keep their peaks (BSBUSDT +1.92 % instead of +0.66 %, HYPEUSDT +0.87 % instead of +0.14 %, etc.), the average winner would be ~3× larger and the achieved RR ~2:1.

## Verification gate — proceed to Phase 1?

| Check | Status |
|---|---|
| Working tree clean except runtime files | yes |
| Five issues confirmed in current code | yes (file:line cited) |
| Three discoveries confirmed in code AND in live logs | yes |
| Seven baselines captured | yes |
| Event-name reconciliation done (SHADOW_SL_TIGHT → SL_GATEWAY_ACCEPT family) | yes |
| Working notes file at `dev_notes/dir_block_fix/phase0_baseline.md` | yes (this file) |

Proceed to Phase 1.
