# Master Investigation Report — Brain Prompt Information Architecture Enrichment

Date: 2026-05-16
Branch: `fix/brain-prompt-enrichment` (from `main`, commit `36e51aa`)
Author: Claude Code (investigation phase)
Phase: 2.5 operator decision gate
Status: AWAITING OPERATOR ENRICHMENT SELECTION

## Executive Summary

The Trading Intelligence MCP brain (Claude CLI invoked via `claude_code_client.py`) makes trade and position decisions using two prompts per cycle: CALL_A (new trades, ~5 min) and CALL_B (position management, offset 2.5 min). The implementation document `IMPLEMENT_BRAIN_PROMPT_ENRICHMENT.md` prescribed an investigation-first audit to identify information gaps that cause measurable trade-quality issues without inflating prompt size or compromising the aggressive-exploitation philosophy.

This investigation read 12,553 lines of source across 8 critical files plus configuration, queried the live trading database (1,755 trade intelligence rows, 50 most-recent closed trades, 57 news articles, 48 daily PnL rows), captured a fresh CALL_A + CALL_B prompt dump from the production system, parsed 884 historical prompt dumps for systematic patterns, and cross-checked every audit finding against current code. Two findings from the source document required major revisions, two findings were definitively confirmed at root-cause level, and one new top-priority issue was discovered.

The single highest-value finding: `## TODAY: PnL=+0.00%` appears in every CALL_B prompt across every single one of the 884 captured dumps spanning weeks of trading sessions and many regimes. The root cause is a service-bootstrap gap — `DailyPnLManager.initialize()` has zero callers in `src/`, and the database confirms all 48 historical `daily_pnl` rows have `starting_equity = 0.0`. The fix is one line at `src/workers/manager.py:1245` (or equivalent) plus a periodic update tick. It is the cheapest highest-value enrichment in the entire batch.

The investigation produced nine subsidiary reports plus this synthesis. The deliverable for operator approval is a prioritized list of seven candidate enrichments. The operator chooses which to implement; this report makes a recommendation but does not pre-decide.

## Investigation Approach

Phase 0 (pre-flight verification) captured baseline metrics and confirmed the running system state. Phase 1 conducted eight parallel investigations (Targets 1-8), each writing a dedicated report with `file:line` citations for every claim. Phase 2 synthesized findings into an information map (Target 9) and this master report (Target 10). No production code was modified.

Investigation followed three hard rules from `IMPLEMENT_BRAIN_PROMPT_ENRICHMENT.md` Part C and the project `CLAUDE.md`:

- Read every file end-to-end before any claim about its behaviour
- Grep all usages of any variable or method before asserting its callers
- Distinguish observed facts from inferences; mark NOT FOUND items explicitly

## Confirmation Or Refinement Of The Source Document's Part-A Findings

### Finding 1 — CALL_A prompt is well-designed for what it includes

**Confirmed.** Fresh dump shows CALL_A at 16,831 user prompt + 6,724 system prompt = 23,555 total chars — within the document's reported 14,989-17,974 range. Section structure matches the document's anatomy. See `01_call_a_anatomy.md`.

### Finding 2 — 38-strategy per-coin vote breakdown is not in the prompt

**Partly refuted.** Per-coin Top-3 BUY voters and Top-3 SELL voters by `conf × weight` already render at `src/brain/strategist.py:1766-1779` via the `_format_briefing_extras` helper. The `[brain].surface_briefing_fields` flag is set to `true` in `config.toml:204`. E1 is therefore ALREADY SHIPPED. The real gaps are vote OPPOSITION (E2) and CATEGORY SPLIT (E3). The actual strategy count is 39, not 38 — `src/strategies/register_all.py:10-109` declares 19 + 20 production strategies plus X1 testnet-only. See `03_layer1_data_production.md` and `08_vote_opposition_audit.md`.

### Finding 3 — K-class meta-strategies all at conf=0.00

**Partly refuted.** Of the four K-class strategies:

- `K1_claude_conviction.vote()` returns conf=0.0 BY DESIGN at `k1_claude_conviction.py:84` — intentional non-voter; K1 is a PRODUCER of signals, not a voter. The K1 trigger mechanism (`altdata["k1_trigger"]`) is PENDING-IMPLEMENTATION: no injector exists anywhere in `src/`. So K1's vote-side is intentional but its scan-side is dormant.
- `K2_pattern_memory.vote()` returns conf=0.0 in production due to MISSING DATA — `pattern_log` table is fully wired in DB (`migrations.py:300`, `learning_repo.py:129-143`) but `strategy_worker._build_altdata` at `strategy_worker.py:389-491` does not query it. K2 always falls into its "no history" branch.
- `K3_ensemble.vote()` returns conf=0.0 BY DESIGN at `k3_ensemble.py:28` — K3 IS the voting system; it cannot vote on itself.
- `K4_adaptive_optimizer.vote()` returns conf=0.0 BY DESIGN at `k4_adaptive_optimizer.py:28` — K4 is the weekly batch optimizer; does not participate in per-cycle voting.

So only K2 is a genuine DATA_MISSING gap (a sub-fix candidate as `prompt-enrich/p3-6`). K1's `scan()` injector is a separate sub-fix candidate. K3 and K4 are correctly inert. See `03_layer1_data_production.md`.

### Finding 4 — CALL_B shows "TODAY: PnL=+0.00%" in every cycle

**Confirmed definitively.** Across all 884 captured stage2 dumps, every single CALL_B prompt contains `## TODAY: PnL=+0.00%`. Root cause traced to `DailyPnLManager` lifecycle:

- `pnl_manager.py:33` initializes `starting_equity = 0.0`
- `pnl_manager.py:201` falls through to `else` branch when `starting_equity <= 0`, forcing `current_pnl_pct = 0.0`
- `initialize()` at `pnl_manager.py:94-95` would set `starting_equity` non-zero, BUT has zero callers in `src/`
- `update()` at `pnl_manager.py:148-149` could set it as fallback, BUT is called only from four operator-triggered Telegram handlers (`telegram/handlers/portfolio.py:22,61`, `telegram/handlers/system.py:29`, `telegram/bot.py:562`) — never by any periodic worker

Live DB confirms: all 48 `daily_pnl` rows have `starting_equity = 0.0`. The trade-close path (`workers/manager.py:1551-1566`) correctly writes `realized_pnl` and `ending_equity`, but `starting_equity` is never set. CALL_B at `strategist.py:3169` reads `current_pnl_pct` which has been stuck at 0.0 since process start. See `05_today_pnl_audit.md`.

### Finding 5 — News articles not in prompt

**Confirmed.** 57 news articles ingested in last 24h, 13 with non-zero sentiment scores. `NewsService` exposes `get_news_summary` (`news_service.py:160-202`) and `get_news_for_symbol` (`news_service.py:134-145`) ready for prompt consumption. `grep "news|finnhub|article" src/brain/strategist.py` returns zero hits in prompt construction. E8 is purely a wiring task — operator deferred. See `07_news_pipeline_audit.md`.

### Finding 6 — TIAS lessons not in prompt

**Revised significantly.** TIAS Phase 1 (capture) is wired and running — 1,755 rows captured. TIAS Phase 2 (DeepSeek analyzer) IS ALSO RUNNING — fired on every close via `workers/manager.py:1789-1796`. The original assumption "Phase 2 has never produced lessons" was wrong: `ds_lessons IS NULL` for all 1,755 BUT `ds_why IS NULL` is 0, `ds_analyzed_at IS NULL` is 0, and `ds_model` is populated with `deepseek/deepseek-chat-v3-0324` on every row.

`ds_lessons` is a VESTIGIAL column from a prior schema. The current DeepSeek prompt (`tias/prompts.py:28-39`) does not ask for it; `TradeAnalyzer._map_response` (`analyzer.py:190-211`) does not write it. The actionable lesson surface — `ds_why`, `ds_category`, `ds_what_should_done`, `ds_how_to_exploit` — IS populated for all 1,755 rows.

The gap is renderer-side only: `strategist.py` does not query `trade_intelligence` for any of these fields. `thesis_manager.get_recent_lessons` at `core/thesis_manager.py:211-227` reads `trade_thesis` (a different table). E6 (recent-loss context bridge) is therefore a pure renderer task plus one new SQL helper sibling to `recent_loss_symbols` at `core/trade_recorder.py:24-52`. The pre-approved `prompt-enrich/p3-0` (TIAS Phase 2 wire-in) is NOT NEEDED and can be removed from the implementation plan. See `04_tias_lessons_pipeline.md`.

### Finding 7 — Strategy category breakdown not shown

**Confirmed.** Strategy categories are queryable via `registry.py:55-57` (`s.category` property). Category split is not pre-aggregated in `_strategy_votes[symbol]` cache but is trivially derivable from existing data. E3 is a renderer + 39-entry map. See `08_vote_opposition_audit.md`.

### Finding 8 — Vote opposition not shown

**Confirmed.** `_strategy_votes[symbol]` cache at `layer_manager.py:110-116` already contains `buy_weighted`, `sell_weighted`, `neutral_weighted` as pre-aggregated weighted sums. E2 is a one-line renderer using already-cached fields. See `08_vote_opposition_audit.md`.

### Finding 9 — RECENT_LOSER_COOLDOWN reaches brain

**Confirmed.** State labels including RECENT_LOSER_COOLDOWN are populated by `scanner_worker._build_coin_package` and render in the per-candidate block in CALL_A. The brain explicitly references this label in dump responses (e.g., ATOMUSDT cycle 1 response cites it). Not a gap.

### Finding 10 — Prompt is honest about uncertainty

**Confirmed.** The "These are HINTS — often wrong. Make your OWN analysis" framing is present in the strategy hints section. Architectural humility preserved.

### New Finding (Not In Document) — Brain rejection rate is 7.3%, not 74%

Across 61 cycles in the available rotation (`grep "Claude new trades:" data/logs/*.log`), 151 trades proposed, 140 executed, 11 rejected — a 7.3% rejection rate. Skip reasons: `survival_block` (7), `gate_rejected` (3), `order_reject` (1). The document's stated 74% baseline is invalid. Phase 4 success metrics must be re-anchored away from rejection-rate-reduction toward `wd_claude_action` loss frequency, prompt-size discipline, and brain reasoning citation count for new fields.

### New Finding — B1a Regime Calibration Not On Main

The implementation document asserts "B1a is verified working, do not touch regime.py". Verification: `config.toml:811-815` on `main` still has the pre-B1a values (`trending_adx_threshold=25`, `ranging_choppiness_threshold=60`, `dead_adx_threshold=15`). The B1a calibration commits (`266c5a6`, `dea18d8`, `3433010`) live on branch `fix/regime-detector-b1a-2026-05-12`, which is also reachable from `fix/j1-orphan-positions` but not from `main`. The currently-running production process (PID 424, started 03:41 today from j-series) HAS B1a. This new `fix/brain-prompt-enrichment` branch, sitting on main, does NOT. Eventual merge requires operator coordination. No action required from this fix beyond awareness.

### New Finding — "Recent Performance" Footer Missing On Main

The fresh CALL_B dump from production renders `## RECENT PERFORMANCE (last 50 closes — directional pattern only)` with WR and close-reason breakdown. The helper `format_aggregated_stats_for_prompt` plus `get_aggregated_stats` were added in commit `5e26007` on branch `fix/five-critical-fixes-2026-05-11`. Neither helper is present in `src/core/thesis_manager.py` on `main` (current size 314 lines). Any enrichment touching CALL_B per-position render path on this branch should account for that footer's eventual merge.

## Proposed Enrichments — Full Trade-off Analysis

### E1 — Per-coin top-3 strategy voter summary

**Status:** Already shipped at `strategist.py:1766-1779` and active in production (`[brain].surface_briefing_fields = true` in `config.toml:204`).

Action required: VERIFY in Phase 4 that the format consistently appears across all 15 candidates and char counts are within budget.

### E2 — Per-coin vote opposition flag

**Source:** `_strategy_votes[symbol]` cache fields `buy_weighted`, `sell_weighted`, `neutral_weighted` at `layer_manager.py:110-116`.

**Proposed format:** `Opposition: STRONG (4 voters at conf>=0.6, wsum=2.8)` — 50-60 chars per coin. STRONG / MODERATE / WEAK tiers based on opposing weight thresholds.

**Cost:** ~60 × 15 = 900 chars CALL_A budget impact.

**Complexity:** Under 1 day. Renderer-only, no schema change.

**Benefit:** Brain explicitly observes ambiguity vs unanimity in ensemble agreement.

**Failure mode:** Brain over-anchors on opposition strength and underrates valid contrarian setups. Mitigation: tier thresholds calibrated against historical opposition distribution.

**Recommendation:** INCLUDE.

### E3 — Per-coin strategy category split

**Source:** `strategy_name → category` map derivable from `registry.py:55-57` (`s.category`).

**Proposed format (non-zero categories only):** `Cats: scalping 2B | momentum 4B | advanced 2B | predatory 1B` — 60-80 chars per coin.

**Cost:** ~80 × 15 = 1,200 chars CALL_A budget impact.

**Complexity:** Under 1 day. Build a derived dict once per cycle + renderer.

**Benefit:** Brain distinguishes one-category-clustered agreement (less robust) from cross-category agreement (more robust).

**Failure mode:** Brain ignores the new data; or overweights category-cluster signal. Both low impact.

**Recommendation:** INCLUDE (operator may defer if budget pressure).

### E4 — TODAY PnL root cause fix (CALL_B)

**Source:** `DailyPnLManager` lifecycle wire-in.

**Fix:**
1. Add `await pnl_manager.initialize()` immediately after the existing service registration at `src/workers/manager.py:1245` (wrapped in try/except so a startup failure does not crash the manager — log a warning and continue with `current_pnl_pct = 0.0` fallback).
2. Add `await pnl_manager.update()` as a 60-second tick inside the existing `EnforcerWorker` (already runs every 60s) for per-cycle unrealized PnL freshness.

**Format:** Same line `## TODAY: PnL=+X.XX%` — just gets the real value.

**Cost:** 0 chars (replaces existing line); fix code 5-15 lines plus tests.

**Complexity:** 1-2 days including unit + integration tests.

**Benefit:** Brain sees real session state every CALL_B; influences hold-vs-close decisions which dominate loss path. Current 50-trade window shows `wd_claude_action` is the worst close category (10 trades, 10% WR, -$144 net).

**Failure mode:** Equity fetch errors propagate to prompt build. Mitigation: try/except + feature flag fallback to skip-the-line behaviour.

**Reversibility:** Feature flag `[brain].emit_today_pnl_in_callb = True` default. Flip to False to revert without code revert.

**Logging:** `TODAY_PNL_COMPUTED | realized=$X unrealized=$Y pct=Z% starting_equity=$E` per CALL_B build.

**Recommendation:** HIGHEST PRIORITY INCLUDE.

### E5 — Direction performance line (CALL_B only)

**Source:** `PerformanceEnforcer._per_direction` at `performance_enforcer.py:65,610-617` plus `trade_history` fallback.

**Proposed format:** `## TODAY DIRECTION PERF: Longs 3W/1L (75% WR, +0.42% avg) | Shorts 2W/3L (40% WR, -0.18% avg)` — 100-120 chars in CALL_B.

**Cost:** +120 chars CALL_B (~3% of 5,000 budget).

**Complexity:** Under 1 day. Data path exists; insertion at `strategist.py:3173`; format + flag.

**Benefit:** Brain observes direction asymmetry when making hold-vs-close decisions.

**Failure mode:** Recency bias risk (small samples). Mitigation: include absolute counts (not just percentages), window = 24h default (not today-only), frame as observation not prescription. Document explicitly forbids re-introducing in CALL_A — E5 is CALL_B only.

**Reversibility:** Feature flag `[brain].emit_direction_perf_in_callb = False` default off. Operator approves framing before flip.

**Logging:** `DIR_PERF_COMPUTED | window=24h longs_n=N1 longs_w=W1 shorts_n=N2 shorts_w=W2 | did=...`

**Recommendation:** INCLUDE (operator approves framing first).

### E6 — Per-coin recent-loss context bridge

**Source:** `trade_intelligence.ds_why`, `ds_category`, `ds_what_should_done` populated for all 1,755 rows. NEW SQL helper needed at `src/core/trade_recorder.py` (sibling to `recent_loss_symbols` at lines 24-52).

**Proposed format:**

In CALL_A per-coin block under RECENT_LOSER_COOLDOWN flag (when applicable, typically 0-5 coins per cycle):

```
RECENT_LOSER_COOLDOWN — Last loss 2026-05-15 16:34 [Short] -0.4% wd_claude. Cause: trend-pullback failed when range-bound. Should: held until 1H structure broke.
```

70-90 chars per flagged coin.

In CALL_B per-position block (when similar pattern history exists, typically 1-3 positions per cycle):

```
Similar setup pattern: 3 of last 5 same-side same-regime closes profitable; this one's age (12 min) is below average winning hold (24 min).
```

80-100 chars per position.

**Cost:** 400 chars CALL_A + 400 chars CALL_B worst case.

**Complexity:** 1-2 days. SQL helper + format + insertion in CALL_A per-coin block + CALL_B per-position block + char-cap each lesson + regime-match filter.

**Benefit:** Brain has concrete "this setup lost because X" rather than just opaque RECENT_LOSER_COOLDOWN label. Direct lift on the dominant loss path (`wd_claude_action`).

**Failure mode:** Lesson text stale or misleading. Mitigation: filter by regime match, last 1-2 trades only, char-cap 80, exclude trades older than 14 days.

**Dependency:** None — Phase 2 IS already running.

**Recommendation:** INCLUDE (operator approves lesson format first).

### E7 — K-class meta-strategy outputs

**K1 outputs** (deep-analysis on STRONG signals): out of scope — requires building the `altdata["k1_trigger"]` injector pipeline from a separate analysis path.

**K2 outputs** (pattern memory): `prompt-enrich/p3-6` candidate. Wire `strategy_worker._build_altdata` to query `pattern_log` table for the last N matching patterns. Complexity 2-3 days. Operator-optional.

**K3/K4 outputs:** intentional non-voters; nothing to bridge.

**Recommendation:** DEFER to a separate fix prompt unless operator wants K2 bundled.

### E8 — News headline integration

**Status:** DEFERRED per operator. Map captured in `07_news_pipeline_audit.md` for future fix prompt.

### E9 — Position aging vs regime drift (CALL_B)

**Source:** regime_detector history + position open time.

**Proposed format:** `Regime drift: opened in trending_up 06:14; ranging from 06:38 (12 min into position).`

**Cost:** 0-100 chars per position (only when drift detected).

**Complexity:** 1 day.

**Benefit:** Brain reads explicit "context changed" cue.

**Failure mode:** Noise if regime detector flips frequently. Mitigation: only render when regime confidence change is significant.

**Recommendation:** DEFER unless operator prioritises.

### E10 — Position similar-trade history (CALL_B)

**Source:** `trade_history` queryable with similarity match (side + regime + hold time).

**Proposed format:** `Similar close pattern: 4 of last 10 same-side same-regime trades held >30 min closed profitably.`

**Cost:** 80-100 chars per position.

**Complexity:** 1-2 days (SQL + format + insertion).

**Benefit:** Brain has pattern data alongside live PnL.

**Failure mode:** Small samples produce misleading rates.

**Recommendation:** DEFER unless operator prioritises (mid-tier value, partially overlaps with E6).

## Recommended Priority Order

Based on `(benefit × probability_of_impact) / (cost × risk)` scoring (see `09_information_map.md` for full table):

1. **E4** — TODAY PnL root-cause fix (score 12.5) — single highest-value lowest-risk
2. **E5** — Direction perf CALL_B (score 8.0) — leverages existing data path
3. **E2** — Vote opposition (score 6.0) — trivial renderer using cached fields
4. **E6** — Recent-loss context bridge (score 4.0) — direct lift on dominant loss path
5. **E3** — Category split (score 2.25) — informative but lower probability of impact

**Optional / defer:**

6. **E7-K2** — K2 pattern bridge (score 1.0) — needs DB query wire-in
7. **E9** — Regime drift (score 1.5)
8. **E10** — Similar-trade history (score 0.67)

**Out of this batch:**

9. **E7-K1** — K1 trigger injector — out of scope this batch
10. **E8** — News bridge — operator scoped out

## Implementation Plan Stub (Conditional On Operator Selection)

Sequencing if operator approves E4, E5, E2, E6, E3 as the top-5 batch:

| Commit | Enrichment | Files Touched | Estimated Days |
|---|---|---|---|
| `prompt-enrich/p3-1` | E4 today_pnl fix | `workers/manager.py`, `strategies/pnl_manager.py`, `workers/enforcer_worker.py` (if separate), `config/settings.py`, new tests | 1-2 |
| `prompt-enrich/p3-2` | E5 dir_perf CALL_B | `brain/strategist.py:3150-3200`, `config/settings.py`, new tests | 1 |
| `prompt-enrich/p3-3` | E2 vote opposition | `brain/strategist.py:1766-1800`, `config/settings.py`, new tests | 1 |
| `prompt-enrich/p3-4` | E6 lesson bridge | `core/trade_recorder.py` (new SQL helper), `brain/strategist.py:1700+ and 3150-3300`, `config/settings.py`, new tests | 1-2 |
| `prompt-enrich/p3-5` | E3 category split | `brain/strategist.py:1766-1800`, `strategies/registry.py` (no change — read existing), `config/settings.py`, new tests | 1 |

**Total estimate:** 5-7 working days for top-5 batch (excluding Phase 4 verification soaks).

Each commit:

- Standalone, revertable
- Feature flag in `[brain]` config section with default behaviour preserved
- Structured log events from the doc's Rule 6 list (`PROMPT_ENRICHMENT_INCLUDED`, `BRAIN_DATA_AVAILABLE`, `STRAT_VOTE_BRIDGE`, `TODAY_PNL_COMPUTED`, `DIR_PERF_COMPUTED`, `TIAS_BRIDGE`)
- Type hints + docstrings on new methods
- Unit tests for new logic; integration tests for cross-component data flow
- Shadow verified working after commit
- Prompt-size budget recomputed: CALL_A < 25,000 chars, CALL_B < 5,000 chars

Note: `prompt-enrich/p3-0` (TIAS Phase 2 wire-in) is REMOVED from the plan — investigation showed Phase 2 IS already running, just writing to different `ds_*` columns than the vestigial `ds_lessons` schema. The operator's pre-approval of p3-0 is no longer needed.

## Open Questions For Operator

1. **E4 fix path choice.** Option A (operator approval recommended): wire `initialize()` at WorkerManager bootstrap + periodic `update()` tick. Option B (alternative): wire on first CALL_B build via lazy init in `_build_position_prompt`. A is cleaner architecture but touches more files. B is single-touch but adds 50-200ms latency per first CALL_B after restart. **Recommendation: Option A.**

2. **E5 window choice.** Default 24h or today-only? 24h is more stable (less recency bias risk). Today-only is the doc's apparent intent. **Recommendation: 24h.**

3. **E5 framing.** The aggressive-framing rewrite removed dir_perf because the prescriptive "AVOID losing direction" framing was recency-biased. The E5 CALL_B re-introduction is FACT-FRAMED (observation, not prescription). Operator confirms the framing rule: render as `Longs 3W/1L (75% WR, +0.42% avg)` with absolute counts; no "you should X" language; no warning labels.

4. **E6 lesson format approval.** Proposed CALL_A line: `Last loss 2026-05-15 16:34 [Short] -0.4% wd_claude. Cause: trend-pullback failed when range-bound. Should: held until 1H structure broke.` Excerpts `ds_why` and `ds_what_should_done`. Operator approves the structure / length / fields included.

5. **E3 scope.** Include in top-5 batch or defer? Lowest-priority of the proposed top-5. Operator decides if budget pressure or simplicity preferred.

6. **K2 pattern bridge (E7-K2).** Operator wants K2 work bundled with this enrichment batch (as `prompt-enrich/p3-6`)? Or defer to a separate fix prompt? K2's brokenness is a real DATA_MISSING gap but K2 output bridging is independent of the E1-E6 prompt enrichments.

7. **B1a merge ordering.** This branch is on main without B1a. Operator's preferred order: (a) merge B1a to main first, then rebase brain-prompt-enrichment on the new main; (b) keep brain-prompt-enrichment on plain main and merge both branches independently; (c) something else.

8. **"Recent Performance" footer.** The CALL_B production prompt rendered by j-series HAS a "## RECENT PERFORMANCE (last 50 closes)" footer that is NOT on main. When brain-prompt-enrichment lands and the operator eventually merges j-series + brain-prompt-enrichment, the footer rendering will become live. Phase 4 verification on this branch will not show it. Operator OK with this two-branch separation or wants coordination?

## Phase 4 Verification Protocol (Operator-Run)

Per-enrichment soak protocol after each commit lands:

**Pre-soak baseline capture** (run once before any soak):

```
cd /home/inshadaliqbal786/trading-intelligence-mcp/
# Capture last 8h baseline metrics
grep "STRAT_CALL_A_END\|STRAT_CALL_B_END\|wd_claude_action\|GATE_REJECT" data/logs/*.log | tail -1000 > /tmp/baseline.txt
# Capture last 50 closes for direction WR / close-reason baseline
sqlite3 -readonly data/trading.db "SELECT side, COUNT(*), SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END), ROUND(SUM(pnl),2) FROM (SELECT * FROM trade_history ORDER BY exit_time DESC LIMIT 50) GROUP BY side;"
```

Baseline already captured in `phase0_baseline.md`.

**Per-enrichment metric capture:**

- **E4 (today_pnl):**
  - Count CALL_B prompts with non-zero TODAY: PnL value in the soak window
  - Count `TODAY_PNL_COMPUTED` log events; verify cardinality matches CALL_B cycle count
  - Brain reasoning citations: grep response text for "today's pnl" / "today is +" / "today is -" — should rise from baseline 0
  - Regression: verify `current_pnl_pct` matches sum of today's `trade_history.pnl` / `starting_equity` × 100

- **E5 (dir_perf):**
  - Count CALL_B prompts containing `## TODAY DIRECTION PERF` line
  - Count `DIR_PERF_COMPUTED` log events
  - Brain reasoning citations: grep response text for "direction" / "longs" / "shorts" — citation rate should rise
  - Direction-aligned trade rate: compare close-reason WR by side pre vs post

- **E2 (vote opposition):**
  - Count CALL_A prompts containing `Opposition:` lines (should be 15 per CALL_A)
  - Brain reasoning citations: grep response for "opposition" / "voters" / "ambiguity" — rise from 0
  - Skip rate on coins with `STRONG` opposition: should be measurable in `TRADE_SKIP` events

- **E6 (lesson bridge):**
  - Count CALL_A prompts containing `Last loss` lines under RECENT_LOSER_COOLDOWN
  - Count CALL_B prompts containing `Similar setup pattern` lines
  - Brain reasoning citations: grep response for citation of specific past trade or `ds_why` excerpt — rise from 0
  - Re-entry rate on RECENT_LOSER_COOLDOWN coins should drop

- **E3 (category split):**
  - Count CALL_A prompts containing `Cats:` lines (should be 15 per CALL_A)
  - Brain reasoning citations: grep response for "category" / "scalping" / "mean-reversion" / "ai-meta" — rise from 0

**Global metrics every soak:**

- Total CALL_A cycles in window
- CALL_A prompt size distribution: should stay below 25,000 chars
- CALL_B prompt size distribution: should stay below 5,000 chars
- CALL_A and CALL_B build latency (should stay within current p50/p90 envelopes)
- Brain rejection rate (currently 7.3%; should remain stable, not the document's 74% target)
- `wd_claude_action` loss frequency in last 50 closes (currently 10/50, 10% WR, -$144 net) — should drop
- DB cascade count: should remain zero
- Shadow continues working

**Soak duration:** 8+ hours minimum per enrichment, ideally a full session (~12-18 hours).

**Verification deliverable:** `dev_notes/brain_enrichment/phase4_verification.md` filled in post-soak by operator with the metric tables.

## Verdict

Investigation complete. The brain is not blind — the prompt is well-designed. But four targeted enrichments (E4, E5, E2, E6) and one optional (E3) close real information gaps with low cost, low risk, and direct leverage on the dominant loss path. Implementation is sequenced over 5-7 working days plus per-enrichment verification soaks.

Two findings from the source document required major revisions: the TIAS pre-requisite fix is unnecessary (Phase 2 is already running), and the 74% rejection-rate target is invalid (current rate is 7.3%). One previously-overlooked priority emerged: the TODAY PnL root cause is a service-bootstrap gap that the original audit framed as a CALL_B observability gap.

The operator now decides at Phase 2.5 which enrichments to include. The plan-mode implementation outline and the priority recommendation are above. Implementation does not begin without explicit operator selection.
