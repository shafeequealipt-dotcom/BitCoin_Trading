# Information Map — System Produces vs Brain Receives

Date: 2026-05-16
Branch: `fix/brain-prompt-enrichment`
Synthesis of Targets 1-8.

## Purpose

Three-column census of every data field relevant to brain trade or position decisions. For each field: what the system produces, what reaches the brain prompt, and the gap category (NO_GAP / GATED / DEAD_CODE / NOT_BRIDGED / DATA_MISSING / DEFERRED).

Gap categories:
- **NO_GAP** — produced and reaches the brain
- **GATED** — produced; deliberately removed by aggressive-framing rewrite (2026-05-05), no config flag
- **DEAD_CODE** — produced; builder method exists but is unused
- **NOT_BRIDGED** — produced and stored; no code path renders it into the prompt
- **DATA_MISSING** — strategy/method exists but its required upstream data is never populated
- **DEFERRED** — gap real but operator scoped this enrichment to a separate fix prompt

## CALL_A Information Map (15 trade candidates per cycle)

| Field | SYSTEM HAS (source) | BRAIN GETS (in CALL_A) | GAP |
|---|---|---|---|
| Global regime + Fear & Greed | `regime_detector.detect()`, `fear_greed.get_latest()` | `Global regime: X (confidence=Y%, F&G=Z)` line 1 | NO_GAP |
| Tradeable coin list (15) | `scanner.get_active_universe()` | `TRADEABLE COINS THIS CYCLE (N coins): [list]` | NO_GAP |
| Per-coin XRAY setup + score + grade | `structure_cache.get_ranked_setups` | full block per candidate | NO_GAP |
| Per-coin SMC details (POC, FVG, OB, MTF) | `structure_cache` | full block | NO_GAP |
| Per-coin regime + confidence | `regime_detector.get_coin_regime` | per-candidate `REGIME=...` | NO_GAP |
| Per-coin indicators (ADX, atr_pct, chop, vol_ratio, RSI, MACD_hist) | `ta_cache.analyze(sym, H1)` | per-coin and market data block | NO_GAP |
| Per-coin state labels (TREND_PULLBACK_SHORT, KILL_ZONE_OPPORTUNITY, RECENT_LOSER_COOLDOWN, etc.) | `scanner_worker._build_coin_package` | per-candidate `state_labels=[...]` | NO_GAP |
| Per-coin interestingness + action hint | `scanner_worker` | per-candidate `interestingness=X.XX action=...` | NO_GAP |
| Per-coin SL/TP/RR levels | `structure_cache` | per-candidate `levels=...` | NO_GAP |
| Per-coin funding rate + OI 4h | `altdata_service` | per-coin in candidate block | NO_GAP |
| Per-coin top-3 strategy voters (BUY/SELL/NEUTRAL) | `layer_manager._strategy_votes` (cache), rendered at `strategist.py:1766-1779` via `_format_briefing_extras` | `Votes: BUY=X vs SELL=Y (N voters)` + Top-3 BUY/SELL block per candidate | NO_GAP (E1 already ships) |
| Per-coin weighted opposition (`buy_weighted`, `sell_weighted`, `neutral_weighted`) | `_strategy_votes[sym]` cache (`layer_manager.py:110-116`) | Top-3 aggregates only; opposition not summarized | NOT_BRIDGED (E2 candidate) |
| Per-coin strategy category split | derivable from `strategy_name` → category map (registry has it; not cached as split) | not rendered | NOT_BRIDGED (E3 candidate) |
| Strategy hints global (top firers across all coins) | `layer_manager._strategy_hints` | "Active categories: ..." + per-coin consensus rendered | NO_GAP |
| K1 deep-analysis signals | `K1.scan()` triggered when `altdata["k1_trigger"]` set | nothing | DATA_MISSING (no `k1_trigger` injector exists) |
| K2 pattern-memory matches | `K2.vote()` reads `altdata["pattern_matches"]` | conf=0.0 always | DATA_MISSING (pattern_log DB table exists in `migrations.py:300` and `learning_repo.py:129-143` BUT `strategy_worker._build_altdata` at lines 389-491 does not query it) |
| K3 ensemble meta | placeholder by design (`k3_ensemble.py:28`) | conf=0.0 | DISABLED_BY_DESIGN (intentional non-voter) |
| K4 adaptive optimizer | weekly batch optimizer (`apex/optimizer.py`); does not vote (`k4_adaptive_optimizer.py:28`) | conf=0.0 | DISABLED_BY_DESIGN |
| News articles (last 24h, per-coin or aggregate) | `news_service.get_news_summary`, `get_news_for_symbol` (`news_service.py:160-202`); 57 articles in last 24h with 13 scored | nothing | DEFERRED (E8 — operator scoped out) |
| Sentiment (F&G only, not news-derived) | `fear_greed.get_latest()` only | F&G value | NO_GAP (news sentiment is DEFERRED) |
| Account state (equity, available, per-trade size, max positions) | `account_service`, `tiered_capital` | `## ACCOUNT` block | NO_GAP |
| Position gate / held symbols | `position_service.get_positions()` | `[POS]` symbols listed | NO_GAP |
| Event buffer / watchdog events | `event_buffer` | up to 20 events appended | NO_GAP |
| Urgent queue / position concerns | `urgent_queue.drain_concerns()` | rendered at tail | NO_GAP |
| Daily PnL summary in CALL_A | `pnl_manager.current_pnl_pct` | NOT rendered (removed by aggressive-framing) | GATED (intentional CALL_A removal; CALL_B-only re-introduction is E4/E5) |
| Direction-specific WR (last N closes by side) | `_build_direction_performance` at `strategist.py:3490-3577` exists; `PerformanceEnforcer._per_direction` at `performance_enforcer.py:65,610-617` aggregates live | NOT rendered (removed by aggressive-framing) | DEAD_CODE (E5 candidate, CALL_B only) |
| Mode line / coaching text / fund rules verbose | various builders exist as dead code | minimal fund block only | GATED (intentional) |
| TIAS lesson context per RECENT_LOSER_COOLDOWN coin | `trade_intelligence.ds_why`, `ds_category`, `ds_what_should_done` populated for all 1,755 rows (Phase 2 IS running) | nothing — no SQL helper, no rendering code | NOT_BRIDGED (E6 candidate; renderer-only fix) |

## CALL_B Information Map (per open position + globals)

| Field | SYSTEM HAS (source) | BRAIN GETS (in CALL_B) | GAP |
|---|---|---|---|
| Regime + sentiment (cached from CALL_A) | `self._last_regime_str`, `self._last_fg_value` | line 1-2 | NO_GAP |
| Today's PnL | `pnl_manager.current_pnl_pct` reads `pnl_manager` service | rendered as `## TODAY: PnL=+0.00%` UNIVERSALLY across 884 dumps | BROKEN_INIT (E4 root cause: `DailyPnLManager.initialize()` has zero callers in `src/`; `starting_equity` stuck at 0.0 → computation falls through `else` branch at `pnl_manager.py:201`) |
| Position management contract (~1064 chars, 53% of CALL_B) | hardcoded in `strategist.py:3184-3205` | full block | NO_GAP |
| Per-position side/entry/now/PnL/SL/TP/lev/age | `position_service.get_positions()` + `thesis_manager.get_open_theses()` | full block per position | NO_GAP |
| Per-position FLIPPED notice with RR comparison | thesis_manager flip data | rendered when applicable | NO_GAP |
| Per-position regime alignment | `regime_detector.get_coin_regime` | `Regime: X Y%` | NO_GAP |
| Per-position SL consumed | derived from price vs SL | rendered | NO_GAP |
| Today direction perf (longs WR vs shorts WR) | `PerformanceEnforcer._per_direction` aggregated | nothing | NOT_BRIDGED (E5 candidate) |
| TIAS lesson context per open position (similar past close pattern) | `ds_why`/`ds_category` available; thesis_manager.get_recent_lessons reads `trade_thesis` not `trade_intelligence` | nothing — `_build_position_prompt` lessons block was deliberately removed at line 3336-3349 | NOT_BRIDGED (E6 candidate; renderer + new SQL helper) |
| Recent performance footer (last 50 closes WR + by-reason breakdown) | dump showed this footer but the helper `format_aggregated_stats_for_prompt` is on a branch NOT MERGED to main | rendered when running on j-series; NOT RENDERED on this branch | DEAD_CODE on main (was on `fix/five-critical-fixes-2026-05-11` per agent A finding; not in current src/) |
| Position aging vs regime drift | `regime_detector.get_coin_regime` + position open time | rendered as static `Regime: X Y%`; no "regime shifted at age Z" framing | NOT_BRIDGED (E9 candidate) |
| Similar past close pattern statistics | `trade_history` queryable; thesis_manager has `get_aggregated_stats` on a non-merged branch | not rendered on this branch | NOT_BRIDGED (E10 candidate) |
| Cooldowns | `coordinator._symbol_cooldowns` | `RECENTLY CLOSED (wait for cooldown)` block | NO_GAP |
| URGENT alerts | `urgent_queue.drain_concerns()` | tail | NO_GAP |

## Enrichment Candidates — Cost/Benefit/Risk

Per-coin cost is amortised over 15 candidates; per-position cost over open positions (typically 3-10).

### E1 — Per-coin top-3 strategy voter summary

- **Status**: ALREADY SHIPPED in production at `strategist.py:1766-1779`
- Cost: included
- Risk: 0
- Benefit: already captured
- Recommended action: VERIFY format consistency and char accounting in Phase 4

### E2 — Per-coin vote opposition flag

- Source: `_strategy_votes[symbol]` cache fields `buy_weighted`, `sell_weighted`, `neutral_weighted` already cached
- Proposed format: `Opposition: STRONG/MODERATE/WEAK (N voters at conf>=0.6, wsum=X.XX)` — 50-60 chars
- Per-coin cost: ~60 chars; 15 coins × 60 = 900 chars
- Complexity: <1 day (renderer-only, no schema change)
- Expected benefit: brain explicitly sees vote ambiguity vs unanimity
- Failure mode: brain over-anchors on opposition strength → underrates valid contrarian setups
- Recommendation: INCLUDE

### E3 — Per-coin strategy category split

- Source: `strategy_name → category` map; live `registry.py:55-57` (`s.category`)
- Proposed format: `Cats: scalping 2B | momentum 4B | advanced 2B | predatory 1B` — 60-80 chars, non-zero categories only
- Per-coin cost: ~80 chars; 15 coins × 80 = 1200 chars
- Complexity: <1 day (derived dict + renderer)
- Expected benefit: brain sees whether agreement is one-category clustered (less robust) or cross-category (more robust)
- Failure mode: brain ignores it (low risk); or overweights category-cluster signal
- Recommendation: INCLUDE (operator may defer if budget pressure)

### E4 — Today PnL root cause fix (CALL_B)

- Source: `pnl_manager.current_pnl_pct`; fix path: wire `await pnl_mgr.initialize()` at `workers/manager.py:1245` + periodic `update()` in EnforcerWorker
- Format: same line, just gets the real value
- Cost: 0 chars (replaces existing line); fix code ~5-15 lines
- Complexity: 1-2 days (init + periodic tick + tests)
- Expected benefit: brain sees real session state every CALL_B; influences hold-vs-close in CALL_B which is the dominant loss path (`wd_claude_action` 10/25 losses, -$144 net)
- Failure mode: equity fetch errors propagate; mitigated by try/except + feature flag
- Recommendation: INCLUDE (highest value lowest risk)

### E5 — Direction performance line (CALL_B)

- Source: `PerformanceEnforcer._per_direction` (`performance_enforcer.py:65,610-617`) + `trade_history` fallback
- Proposed format: `## TODAY DIRECTION PERF: Longs 3W/1L (75% WR, +0.42% avg) | Shorts 2W/3L (40% WR, -0.18% avg)` (CALL_B only)
- Cost: ~120 chars in CALL_B (1 line)
- Complexity: <1 day (data path exists; format + insertion + flag)
- Expected benefit: brain sees direction asymmetry in close decisions
- Failure mode: recency bias (small samples → erratic). Mitigation: include absolute counts, frame as observation not prescription, window = 24h default
- Recommendation: INCLUDE (operator approves framing first)

### E6 — Per-coin recent-loss context bridge

- Source: `trade_intelligence.ds_why`, `ds_category`, `ds_what_should_done` populated for 1,755 rows. SQL helper needed sibling to `recent_loss_symbols` in `trade_recorder.py:24-52`
- Per-coin cost: 70-90 chars × N flagged coins (typically 0-5)
- Complexity: 1-2 days (SQL helper + format + insertion in CALL_A per-coin block under RECENT_LOSER_COOLDOWN flag, and CALL_B per-position block alongside FLIPPED notices)
- Expected benefit: brain has concrete "this setup lost because X" rather than just "RECENT_LOSER_COOLDOWN" label
- Failure mode: lesson text stale or misleading. Mitigation: filter by regime match, last 1-2 trades only, char-cap each lesson at 80 chars
- Dependency: NONE — Phase 2 IS already running and populating `ds_*` fields (revised from prior assumption)
- Recommendation: INCLUDE (operator approval on format)

### E7 — K-class meta-strategy outputs

- K1 outputs (claude conviction history): would require new infrastructure to inject `altdata["k1_trigger"]` from a separate analysis path. Out of scope for this batch.
- K2 outputs (pattern memory): would require populating `altdata["pattern_matches"]` from `pattern_log` table via `strategy_worker._build_altdata`. Sub-fix candidate as commit `prompt-enrich/p3-6` (operator-optional).
- K3/K4: intentional non-voters; no outputs to bridge.
- Recommendation: DEFER to a separate fix prompt unless operator wants K2 work bundled.

### E8 — News headline integration

- DEFERRED per operator.
- Map: ingestion works (57 articles/24h), scoring sparse, `get_news_summary` ready, zero prompt path. 30-50 lines to bridge.
- Recommendation: defer to a separate fix prompt.

### E9 — Position aging vs regime drift (CALL_B)

- Source: regime_detector + position open time
- Format: `Regime drift: opened in trending_up at 06:14; ranging from 06:38 (24 min ago)` if regime changed since open
- Per-position cost: 0-100 chars (only when drift detected)
- Complexity: 1 day (regime history lookup + position open time comparison + format)
- Expected benefit: brain reads explicit "context changed" cue
- Failure mode: noise if regime detector flips frequently
- Recommendation: DEFER unless operator prioritises (mid-tier value)

### E10 — Position similar-trade history (CALL_B)

- Source: `trade_history` queryable; helper needs to fetch "of last N similar (same side, same regime) closes, X% closed profitably"
- Per-position cost: 80-100 chars
- Complexity: 1-2 days (SQL + format + insertion)
- Expected benefit: brain sees pattern data alongside live PnL
- Failure mode: small samples produce misleading rates; mitigate with absolute counts
- Recommendation: DEFER unless operator prioritises

## Priority Score

Score = (benefit × probability_of_impact) / (cost × risk). Higher is better.

| Enrichment | Benefit (1-5) | Prob of impact (1-5) | Cost (1-5) | Risk (1-5) | Score | Recommendation |
|---|---|---|---|---|---|---|
| E4 today_pnl fix | 5 | 5 | 2 | 1 | 12.5 | HIGHEST PRIORITY |
| E5 dir_perf CALL_B | 4 | 4 | 1 | 2 | 8.0 | SECOND PRIORITY |
| E1 voters (already shipped) | 4 | 4 | 0 | 0 | N/A | VERIFY |
| E2 opposition | 4 | 3 | 1 | 2 | 6.0 | THIRD PRIORITY |
| E6 lesson bridge | 4 | 4 | 2 | 2 | 4.0 | FOURTH PRIORITY |
| E3 category split | 3 | 3 | 2 | 2 | 2.25 | FIFTH (optional) |
| E7-K2 pattern bridge | 3 | 3 | 3 | 3 | 1.0 | DEFER |
| E9 regime drift | 3 | 2 | 2 | 2 | 1.5 | DEFER |
| E10 similar-trade history | 3 | 2 | 3 | 3 | 0.67 | DEFER |
| E8 news bridge | 4 | 3 | 3 | 3 | 1.33 | DEFER (operator scoped out) |

## Prompt Size Budget Math

Current baseline (from fresh dump):
- CALL_A: 16,831 user chars + 6,724 sys chars = 23,555 total. Doc cap: 25,000 CALL_A.
- CALL_B: 2,009 user chars + 1,783 sys chars = 3,792 total. Doc cap: 5,000 CALL_B.

Proposed enrichments aggregate cost (worst case):
- E2 opposition: +900 chars CALL_A
- E3 category: +1,200 chars CALL_A
- E5 dir_perf: +120 chars CALL_B
- E6 lesson bridge: +400 chars CALL_A (5 flagged coins × 80) + 400 chars CALL_B (5 positions × 80)

Post-enrichment CALL_A worst case: 16,831 + 900 + 1,200 + 400 = 19,331 chars (well under 25,000 cap).
Post-enrichment CALL_B worst case: 2,009 + 120 + 400 = 2,529 chars (well under 5,000 cap).

Budget safe across all top-5 priorities.

## Aggregate Statistics

- Total fields catalogued: 28 in CALL_A, 13 in CALL_B
- NO_GAP fields: 23 (well-served today)
- GATED (intentional removal): 3
- DEAD_CODE: 2
- NOT_BRIDGED: 7 (E2, E3, E5, E6, E9, E10, and the merged-only "recent performance footer")
- DATA_MISSING: 2 (K1 trigger pipeline, K2 pattern_matches)
- DISABLED_BY_DESIGN: 2 (K3, K4)
- BROKEN_INIT: 1 (today_pnl)
- DEFERRED: 1 (news)

## Key Findings vs Document's Audit

1. **Document's Finding 4** (TODAY PnL=+0.00% always): **CONFIRMED definitively**. Root cause traced to `pnl_manager.initialize()` never being called. 48 daily_pnl rows ALL have `starting_equity = 0.0`. Fix is mechanical and reversible.
2. **Document's Finding 2** (38-strategy votes not in prompt): **PARTLY REFUTED**. Top-3 voters per coin already render at `strategist.py:1766-1779` (E1 shipped). The real gap is OPPOSITION + CATEGORY SPLIT (E2, E3).
3. **Document's Finding 3** (K-class all conf=0.00): **PARTLY REFUTED**. K1/K3/K4 are intentional non-voters by code design. Only K2 is a real DATA_MISSING gap. K1's `scan()` is PENDING (no trigger injector exists).
4. **Document's Finding 6** (TIAS lessons not in brain): **REVISED**. TIAS Phase 2 IS running. `ds_why`, `ds_category`, `ds_what_should_done` populated for all 1,755 rows. `ds_lessons` is a VESTIGIAL column from a prior schema. E6 is a RENDERER-ONLY fix; no Phase 2 wire-in needed. The operator's pre-approved `prompt-enrich/p3-0` (Phase 2 wire-in) is therefore NOT NEEDED.
5. **Document's Part H baseline** (74% rejection rate): **INVALID**. Current rejection rate is 7.3% across 61 cycles. Success metrics must be re-anchored to `wd_claude_action` loss frequency and prompt-size discipline.
6. **New finding** (not in doc): "## RECENT PERFORMANCE" footer in CALL_B fresh dump from production is rendered by a helper that exists on `fix/five-critical-fixes-2026-05-11` and `fix/j1-orphan-positions` but NOT on `main`. This branch (`fix/brain-prompt-enrichment`) sits on main and is missing that helper. Any enrichment that touches CALL_B per-position rendering must coordinate with the eventual merge of the other branches.
