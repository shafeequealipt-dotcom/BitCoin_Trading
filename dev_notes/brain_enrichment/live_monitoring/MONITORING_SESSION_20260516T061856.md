# Live Monitoring Session — Brain Prompt Enrichment Phase 2

**Session start:** 2026-05-16 06:18:56 UTC
**Branch:** `fix/brain-prompt-enrichment`
**Workers process:** PID 431 started Sat May 16 06:15:37 2026
**Sentinel-gated dumper:** ENABLED (`data/stage2_dumps/.enabled` present)
**Pre-session dump count:** 884
**Active enrichment flags (read from `config.toml` at boot):**
- `surface_top_n_voters = 10` (E1)
- `emit_vote_opposition = true` (E2)
- `emit_category_split = true` (E3)
- `emit_direction_perf_in_callb = true` (E5)
- `emit_recent_loss_context = true` (E6)

## Boot-state observations

The first `STRAT_CALL_A_START` after the restart fired at `06:17:44.012` (decision id `d-1778912264012`) at `strategist.py:657`. The call short-circuited immediately:

```
06:17:44.012 INFO  STRAT_CALL_A_START     | did=d-1778912264012
06:17:44.012 WARN  STRAT_CALL_A_SKIPPED   | reason=no_packages_available count=0
06:17:44.013 INFO  STRAT_CALL_A_END       | el=0ms trades=0 skipped=Y
```

**Interpretation:** scanner_worker has not yet completed its first briefing cycle, so `layer_manager._coin_packages` is empty. Brain correctly skips without building a prompt. This is the documented cold-start behaviour, not a regression.

**Confirmed running my branch:** `STRAT_CALL_A_START` is logged at `strategist.py:657`. On main HEAD this same event logged at `strategist.py:747`. The line-number shift matches the diff stat of `+501 / -34` lines my commits added to strategist.py.

## Anomaly observed during cold-start phase

**Time:** 06:17 – 06:23 (still ongoing as of capture write)
**Symptom:** Two consecutive `STRAT_CALL_A_SKIPPED | reason=no_packages_available count=0` events at 06:17:44 and 06:22:44. Scanner_worker has not yet produced any packages 7 minutes after boot.

**Root cause trace from `workers.log`:**

```
06:17:44.011 CYCLE_RESUME_WAIT     | next_boundary_in_sec=136 reason=cold_start_after_toggle
06:22:14.414 WORKER_NEVER_TICKED   | name=scanner_worker tier=LAYER1D cycle_gated=True
                                     elapsed_since_start_s=390 sweet_spot_fires=1
06:22:44.415 WORKER_NEVER_TICKED   | name=scanner_worker tier=LAYER1D cycle_gated=True
                                     elapsed_since_start_s=420 sweet_spot_fires=1
```

The cold-start resume gate (memory `project_cold_start_resume_fix.md`, shipped 2026-04-29 with `CYCLE_RESUME_WAIT`) is suppressing scanner_worker's tick until the next M5 boundary. The scheduler did fire scanner once (`sweet_spot_fires=1`) but the cycle-gate blocked it. Scanner's sweet-spot offset is M:04 within each 5-min boundary; next attempt = 06:24:04.

This is **not** a regression caused by my commits — the same pattern is documented in the operational memory. The CALL_A skip at line 678 of strategist.py is the **correct cold-start response**: empty `coin_packages` → skip the build rather than send the brain a half-formed prompt.

**Brain-prompt-enrichment impact:** zero. None of my 5 enrichments enter the code path until a real CALL_A or CALL_B builds. Cold-start skip is gated upstream of the prompt builder.

**Watching for:** the first CALL_A that emits `STRAT_AGGRESSIVE_FRAMING` (i.e. the prompt actually built). That's the first cycle where my enrichments will exercise.

## Cycle-by-cycle captures

Each successful CALL_A and CALL_B will be appended below as events arrive from the monitor.

---

### Cycle 1 — CALL_A — `d-1778912864167`

**Timeline:**
- 06:27:44.168 `STRAT_CALL_A_START`
- 06:27:44.169 `PROMPT_ENRICHMENT_INCLUDED | call=CALL_A top_n_voters=10 vote_opposition=True category_split=True recent_loss_context=True flagged_coins=0 packages=10`
- 06:27:44.524 `STRAT_PROMPT_SIZE | sections=70 chars=21342`
- 06:27:44.525 `STRAT_AGGRESSIVE_FRAMING | mode_line=skipped coaching=skipped fund_rules=minimal today_perf=skipped dir_perf=skipped regime_instr=minimal contract=aggressive_exploit zero_two_flag=True`
- 06:29:47.867 `STRAT_CALL_A_END | el=123699ms trades=3`
- 06:29:47 dump `20260516T062947_call0001_d-1778912864167.json` written

**Dump metadata:**
- prompt_hash = `c140ef84ca7d`
- prompt_chars = 15,378 (user prompt)
- sys_prompt_chars = 8,651
- response_chars = 2,619
- elapsed_ms = 123,328 (~2 min)

**Enrichment marker counts in the prompt:**
- E1 Top-10 lines: 6
- E2 Opposition lines: 6
- E3 Cats lines: 6
- E6 Past loss lines: 0 (`flagged_coins=0` — no RECENT_LOSER_COOLDOWN candidates this cycle)
- Vote summary lines: 6 (matches the 6 fully-enriched candidates)

Six candidates carried full vote enrichments (those with `layer_manager._strategy_votes` populated by L3 ensemble cycles); four others rendered base candidate blocks only. This is expected during early post-restart minutes — those four symbols hadn't seen a STRONG ensemble cycle yet.

**Tradeable coin list:** AAVEUSDT, ALGOUSDT, ALICEUSDT, APTUSDT, EGLDUSDT, ETHUSDT, GALAUSDT, IMXUSDT, LDOUSDT, LINKUSDT, MANAUSDT, NEARUSDT, ONDOUSDT, SANDUSDT, XRPUSDT

**Candidate blocks rendered:** GALAUSDT, MANAUSDT, ONDOUSDT, LDOUSDT, AAVEUSDT, IMXUSDT, ETHUSDT, SANDUSDT, NEARUSDT, LINKUSDT (matches `top_n_to_brain=10`).

#### Sample candidate block (MANAUSDT — exercises E1, E2, E3)

```
### MANAUSDT — interestingness=0.60 score=0.41 [TREND_PULLBACK_SHORT]
  XRAY: setup=bearish_fvg_ob conf=0.55 dir=short score=100 quality=A+
  Structure: market_structure=downtrend range_pos=0.68 smc_confluence=55
  SMC: fvg=n/a@0.0909, ob=n/a@0.0914
  MTF: quality=maximum score=9 factors=7
  Volume profile: poc=0.0990 fib_key=0.0951
  Session: asian late manipulation_likely=False
  Levels: SL=$0.0921 TP=$0.0869 RR=2.24
  Signal: type=buy conf=0.47 source=intelligence_aggregator
  Components: fear_greed=31.000, oi_change_pct=-1.486, funding_rate=0.000, ...
  Regime: trending_down conf=0.72 ADX=36.2 atr_pct=63 chop=41 vol_ratio=0.01 trend_dir=-1
  Active categories: scalping, momentum, advanced, predatory, cross_market, time_based, ai_enhanced
  Strategies: 0 fired, ensemble NONE, total_score 0.0
  Votes: BUY=0.00 vs SELL=4.77 (38 voters)
    Top-10: F1_support_resistance(S 0.72), A3_bb_squeeze(S 0.70), B3_ichimoku(S 0.70), B4_double_bottom_top(S 0.70), H3_vol_switch(S 0.70), A4_ema_crossover(S 0.65), A2_vwap_bounce(S 0.60), A1_rsi_reversal(N 0.30), B2_supertrend(N 0.30), C2_rsi_divergence(N 0.30)
    Opposition: NEGLIGIBLE — 0 BUY voters at conf>=0.6 (opp_wsum=0.00 vs agree_wsum=4.77)
    Cats: scalping 3S, momentum 2S, advanced 1S, microstructure 1S
  State: cleanness=0.41 confluence=2 top_components=[confluence=0.20, label_strength=0.17, structural_quality=0.14]
  Score: total=63.9 grade=B | base=33.0/40 confluence=15.0/25 context=3.0/20 quality=12.9/20
  Funding: 0.0001 (longs_paying) OI_4h=+0.00% F&G=31
  Action hint: Short-side pullback continuation. Enter on retest of OB; SL above OB high; TP previous swing low.
```

The three new lines (`Top-10`, `Opposition`, `Cats`) sit directly under the existing `Votes` summary, exactly as designed. The 38 voter count comes from the live ensemble (all 38 strategies that voted in this cycle).

#### Claude's response — citations of new fields

Claude returned 3 trades. Reasoning text directly cites the new enrichments by name:

**ETHUSDT (Sell):**
> "Votes: SELL=5.58 vs BUY=0.00 with **NEGLIGIBLE opposition** across **4 categories (scalping 3S, microstructure 2S, momentum 2S, advanced 1S)**. **F1_support_resistance fired at 0.93 conf** — highest single-voter confidence across all candidates."

**LDOUSDT (Sell):**
> "Votes: SELL=4.80 vs BUY=0.00 **NEGLIGIBLE opposition**. **G4_whale_shadow(S 0.75)** top voter — predatory signal confirming institutional selling. **4 categories aligned (momentum 3S, scalping 2S, microstructure 1S, predatory 1S)**."

**MANAUSDT (Sell):**
> "Votes: SELL=4.77 vs BUY=0.00 **NEGLIGIBLE opposition**. **7 SELL voters fired including F1_support_resistance(S 0.72), A3_bb_squeeze(S 0.70), B3_ichimoku(S 0.70)**. **4 categories aligned (scalping 3S, momentum 2S, advanced 1S, microstructure 1S)**. ... Slightly smaller size due to lower interestingness score and signal showing buy conf=0.47 conflict."

**Citation rollup:**
- E1 (named-voter citations) used in **3 of 3** trade reasonings — every trade names at least one specific voter
- E2 (opposition citations) used in **3 of 3** trade reasonings — explicit "NEGLIGIBLE opposition" language
- E3 (category citations) used in **3 of 3** trade reasonings — explicit category-count phrasing
- E6 (lesson citations) — N/A this cycle (no RECENT_LOSER_COOLDOWN flagged)
- Bonus: brain explicitly noted a SIGNAL-vs-VOTE conflict on MANAUSDT ("signal showing buy conf=0.47 conflict") — the kind of cross-block reasoning the enrichments are meant to enable

**Verdict for Cycle 1:** All three CALL_A enrichments (E1, E2, E3) are being read AND used by the brain. The brain's reasoning is now grounded in specific named voters, explicit opposition strength, and category-level diversification — the three exact information gaps the source document identified.

#### Cycle 1 — Downstream trade execution (anomaly captured)

```
06:30:26 ORDER_START sym=LDOUSDT side=Sell qty=1408.8 ...
06:30:26 ERROR Claude trade failed for LDOUSDT: InvalidOrderError:
         Unsupported symbol: LDOUSDT | details={'symbol': 'LDOUSDT',
         'supported': ['BTCUSDT', 'ETHUSDT']}
06:30:26 ERROR Claude trade failed for MANAUSDT: same error
06:30:26 (ETHUSDT also failed in same batch — confirmed from full log)
06:30:26 Claude new trades: 0/3 executed | skipped={exception=3}
```

**Anomaly:** the trading backend (Shadow/Bybit-demo adapter) reports its supported-symbol list as **only `['BTCUSDT', 'ETHUSDT']`**, but the scanner_worker passes a 15-coin universe to the strategist. Claude can never get a trade past execution unless it picks one of those two — and even ETHUSDT failed in this cycle, suggesting the backend is in a degraded state. Account fetch immediately after reports `equity=0.00 available=0.00 margin_used=0.00`.

**Scope:** out of brain-prompt-enrichment scope. This is a Shadow / Bybit-demo adapter issue (almost certainly tied to a missing post-restart re-handshake with the demo exchange or a stale supported-symbol cache from a prior dev run). The implementation document explicitly listed "the Bybit demo HTTP/auth/signing/WS-parse layer" and "the Shadow adapter" as OUT OF SCOPE. The brain prompt enrichments work; downstream order placement is broken on its own axis.

**Impact on this monitoring session:**
- E1/E2/E3 still verified — CALL_A built and consumed by Claude correctly
- E5 will not surface today's direction perf until closes accumulate (and closes can't happen without successful entries)
- E6 will not surface lessons until a coin in the candidate list overlaps a recent loss with `ds_why` (the 1,755 historical lessons are still queryable, but RECENT_LOSER_COOLDOWN gating depends on the active scanner pipeline)

**Recommendation to operator:** investigate the Bybit-demo adapter's supported-symbol whitelist and account-fetch path. Likely needs a re-handshake or `demo=True` argument confirmation per the project memory `project_bybit_demo_adapter_status.md`. Separate fix prompt scope.

---

### Cycle 2 — CALL_A — `d-1778913287945`

**Timeline:**
- 06:34:47.944 `BRAIN_CYCLE_A | Finding new trades`
- 06:34:47.945 `STRAT_CALL_A_START`
- 06:34:47.946 `PROMPT_ENRICHMENT_INCLUDED | top_n_voters=10 vote_opposition=True category_split=True recent_loss_context=True flagged_coins=0 packages=10`
- 06:34:49.415 `STRAT_PROMPT_SIZE | sections=69 chars=22288`
- 06:34:49.416 `STRAT_AGGRESSIVE_FRAMING | ...`
- 06:36:32.215 `STRAT_CALL_A_END | el=104271ms trades=3`

**Dump:** `20260516T063632_call0002_d-1778913287945.json`
- prompt_hash = `3428347606ff`
- prompt_chars = 16,529 (user) + 8,651 (sys) = 25,180 total
- response_chars = 2,120
- elapsed = 102,787 ms (~1.7 min)

**Enrichment marker counts (up from Cycle 1):**
- E1 Top-10 lines: **8** (was 6 in cycle 1 — 2 more coins now have vote caches)
- E2 Opposition lines: **8**
- E3 Cats lines: **8**
- Vote summary lines: **8**
- E6 lessons: 0 (still no RECENT_LOSER_COOLDOWN flagged)

The cache warming as more L3 ensemble cycles complete is visible — Cycle 2 has 33% more enriched candidates than Cycle 1.

#### Claude's response (full, this cycle)

```json
{
  "new_trades": [
    {
      "symbol": "MANAUSDT", "direction": "Sell",
      "stop_loss_price": 0.0921, "take_profit_price": 0.0869,
      "max_hold_minutes": 45, "leverage": 4, "size_usd": 6,
      "reasoning": "TREND_PULLBACK_SHORT: regime trending_down conf=0.72,
        ADX=36.0, STRONG ensemble score=63.9, SELL=5.27 vs BUY=0.00 with
        NEGLIGIBLE opposition. Multi-category agreement (scalping 3S,
        momentum 2S, advanced 1S, microstructure 1S, time_based 1S).
        Top voters A3_bb_squeeze(S 0.70), B3_ichimoku(S 0.70),
        B4_double_bottom_top(S 0.70). OB at 0.0914, RR=2.24. Chop=41
        confirms trend not choppy."
    },
    {
      "symbol": "LDOUSDT", "direction": "Sell", ...
      "reasoning": "TREND_PULLBACK_SHORT: regime trending_down conf=0.72,
        ADX=36.1, STRONG ensemble, SELL=4.80 vs BUY=0.00 NEGLIGIBLE
        opposition. G4_whale_shadow(S 0.75) is top voter indicating smart
        money selling. Multi-category (momentum 3S, scalping 2S,
        microstructure 1S, predatory 1S). OB at 0.3635, chop=38 confirms
        clean trend."
    },
    {
      "symbol": "BCHUSDT", "direction": "Sell", ...
      "reasoning": "RANGE_FADE_SHORT at range_pos=0.81: STRONG ensemble
        score=63.9, SELL=4.02 vs BUY=0.00, NEGLIGIBLE opposition.
        F1_support_resistance(S 0.72) confirms key resistance.
        Multi-category (momentum 3S, advanced 1S, microstructure 1S,
        scalping 1S). OB at 432.6, RR=3.36. MTF quality=maximum score=9."
    }
  ],
  "focus_coins": ["MANAUSDT","LDOUSDT","BCHUSDT","ONDOUSDT"],
  "avoid_coins": ["GALAUSDT","HYPERUSDT"]
}
```

**Citation rollup for Cycle 2:**
- E1 named voters used in **3 of 3** trades (A3_bb_squeeze, B3_ichimoku, B4_double_bottom_top, G4_whale_shadow, F1_support_resistance)
- E2 opposition explicit in **3 of 3** trades — "NEGLIGIBLE opposition" referenced verbatim
- E3 category split explicit in **3 of 3** trades — "Multi-category (momentum 3S, scalping 2S, ...)" phrasing

**Downstream execution (same broken adapter as Cycle 1):**
```
06:36:49 TRADE_SKIP sym=MANAUSDT rsn=exception InvalidOrderError: Unsupported symbol
06:36:51 TRADE_SKIP sym=LDOUSDT  rsn=exception InvalidOrderError: Unsupported symbol
06:36:53 TRADE_SKIP sym=BCHUSDT  rsn=exception InvalidOrderError: Unsupported symbol
06:36:53 BRAIN_DO_DONE el=21s
```
All 3 trades again rejected by the Bybit-demo adapter (supported = `['BTCUSDT', 'ETHUSDT']` only). 0/3 executed. Same anomaly as Cycle 1. Brain prompt enrichments unaffected; execution-layer issue persists.

---
