# Identified Issues

## Infrastructure & Deployment

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 1 | **Secrets stored in settings.json** — API keys hardcoded in `~/.claude/settings.json` as plaintext | Moved all secrets to `~/.zshrc` env vars, `settings.json` references `$VAR` syntax (but env vars are not expanded by the editor — must be read at runtime from environment) | ✅ Fixed |
| 2 | **OpenRouter API key missing from VM** — Brain (Cloudflare GLM) had no credentials, failed silently | Added `OPENROUTER_API_KEY` to the VM's `.env` file, wired into the OpenAI-compatible client | ✅ Fixed |
| 3 | **Model name not recognized by OpenRouter** — `claude-sonnet-4-20250514` and `anthropic/claude-sonnet-4-20250514` both rejected | Switched to `deepseek/deepseek-chat` (DeepSeek V3) which is free on OpenRouter and handles JSON output well | ✅ Fixed |
| 4 | **Bybit paper-trading credentials failing** — Shadow mode couldn't authenticate | Set `mode = "shadow"` in `config.toml` to match the Bybit paper/demo credentials stored in env vars | ✅ Fixed |
| 5 | **Cold start boundary delays** — After restart, system takes ~5 min to warm up; coin packages unavailable | Natural behavior — the scanner must run before the brain. The 5-min cycle is designed for this | ⚠️ By design |
| 6 | **Worker liveness failures after restarts** — Rapid restarts (3x in quick succession) caused workers to fall behind | Staggered restarts; wait for cold-start boundary before checking health | ✅ Mitigated |

## Configuration & Thresholds

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 7 | **Config mode mismatch** — Mode not set to `shadow` for Bybit demo credentials | Set `mode = "shadow"` in top-level config | ✅ Fixed |
| 8 | **Signal thresholds too strict** — `fg_direction_neutral = true` blocking funding signals; `buy_threshold = 0.3`, `funding_min_active = 0.5`, `oi_min_active = 0.5` too high for the 28-coin universe | Lowered: `fg_direction_neutral = false`, `buy_threshold = 0.05`, `funding_min_active = 0.05`, `oi_min_active = 0.05` | ✅ Fixed |
| 9 | **Ladder arm at +0.2% locks sub-fee profits** — `min_profit_to_arm_ladder_pct = 0.2` arms the breakeven floor at just +0.2%, which is below the ~0.11% round-trip fee. Locks in net losses on small winners | Raised to 0.4% — winners breathe before profit-locking engages | ✅ Fixed |
| 10 | **Trail activation at +0.2%** — `min_profit_for_trail_pct = 0.2` activates the Chandelier trail too early, tightening stops before the move develops | Raised to 0.4%, synced with ladder arm | ✅ Fixed |
| 11 | **Dead regime trail factor 0.6 too tight** — Low-volatility coins get the tightest trail, cutting winners prematurely | Changed to 0.85 — modest protection without strangling the move | ✅ Fixed |
| 12 | **SLTPValidator max_distance_pct = 15% rejects volatile micro-cap coins** — Brain-generated SL at 17-34% from price for MMTUSDT, ESPORTSUSDT, XPINUSDT on testnet | Increased to 25% for testnet | ✅ Fixed |

## Shadow / Testnet

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 13 | **"Symbol not tracked" — shadow server unknown coins** — Main bot's 28-coin scanner universe includes coins (XLMUSDT, METAUSDT, WIFUSDT, etc.) not tracked by shadow's top-100-by-volume list | Added `FORCE_INCLUDE` set to `coin_selector.py`; manually inserted missing coins into `shadow.db` | ✅ Fixed |
| 14 | **Shadow server restart overwrites DB inserts** — `CoinSelector._save_to_db()` marks all as `is_active=0` then upserts top-N from Bybit, wiping manual additions | `FORCE_INCLUDE` set ensures these coins are always reactivated after `select_top_coins()` runs | ✅ Fixed |
| 15 | **"No price available" for newly-tracked coins** — Shadow server needs time to collect market data for newly added coins | Self-resolving — data arrives on the next collector tick | ⏳ Temporary |

## Brain Prompt

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 16 | **Inverted R:R ratio** — Avg win +0.32% vs avg loss -0.50% (0.64x ratio). The prompt told the brain to set TP at 0.3-0.5% for dead/low vol coins but gave no SL guidance; SL ended up wider than TP | Added explicit "TP at minimum 2x SL distance" rule, dead vol SL ranges (0.3-0.5%), and "never set SL wider than TP" instruction (commit `d1b1561`, 2026-07-14) | ✅ Fixed — ⏳ live measurement pending (needs ~100 post-fix closed trades) |
| 17 | **No minimum R:R enforcement in prompt** — The original volatility-adaptive targets only specified TP ranges per volatility class but no SL guidance | Complete rewrite of rule 7 with per-class SL ranges and mandatory 2:1 R:R | ✅ Fixed |
| 18 | ~~**"win_prob_near_certain" trades are the worst performers"**~~ **MISDIAGNOSIS — CORRECTED 2026-07-15.** This is not brain overconfidence. `win_prob_near_certain` (`src/risk/time_decay_sl.py:98`, `near_certain_loser_p_win = 0.10`) is the watchdog's **near-certain-LOSER cut** — it force-closes positions whose modeled win probability has dropped to ≤10%. Its 10% realized win rate matches the ≤10% cut threshold exactly: the model is well-calibrated and salvaging bad positions (~-0.85% median) instead of letting them ride to the full stop (~-1.8%). Confirmed independently in `ENTRIES_QUALITY_DIAGNOSIS.md` Finding 2 and the 371-trade VM re-analysis (2026-07-15): `win_prob_near_certain` netted -$51.16 over 20 trades, but this is bad *entries* being correctly cut, not a closer defect | No action needed — working as designed | ✅ Not a bug |

## Performance (376 Closed Trades)

| Metric | Value | Benchmark |
|--------|-------|-----------|
| Win rate | 59.3% | Good |
| Avg win | +0.32% | Too low |
| Avg loss | -0.50% | Too high |
| Avg win / Avg loss | 0.64x | Needs > 1.5x |
| Total PnL | -4.59% | Needs > 0 |
| Max win | +4.43% | Healthy |
| Max loss | -2.70% | Within cap |

### Close Reason Breakdown

| Reason | Trades | Avg PnL | Assessment |
|--------|--------|---------|------------|
| shadow_sl_tp | 271 | +0.08% | Marginal — barely profitable |
| loss_stall | 37 | -0.04% | Working well, small losses |
| win_prob_near_certain | 20 | **-0.56%** | Worst category — overconfidence |
| deadline_breakeven | 11 | +0.11% | Acceptable |
| strategic_review | 9 | -0.12% | Minor |
| timeout | 8 | -0.33% | Needs monitoring |
| loss_cap_force | 6 | **-1.22%** | Emergency brake working |
| loss_spike_force | 5 | -0.20% | Acceptable |
| monotonic_grind_cut | 4 | -0.39% | Needs monitoring |

## Entry Quality — Volume-Ratio Gate (2026-07-15)

A 371-trade VM analysis (2026-07-11..14, `trade_intelligence`) found the first
entry-time feature that separates winners from losers, after the June
diagnosis (`ENTRIES_QUALITY_DIAGNOSIS.md`) found none: **`volume_ratio`**
(M5 current volume vs its SMA). `vr >= 0.4` split the book +$49.31 kept vs
-$71.17 dropped, and survived 5 robustness checks (per-day, leave-one-symbol-
out, within-symbol, threshold sensitivity, chronological halves). Full
evidence and phased rollout in `IMPLEMENT_ENTRY_VOLUME_GATE.md`.

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 19 | **No entry-time feature predicts trade outcome** — X-RAY confidence, signal confidence, ensemble agreement, regime confidence, ADX all statistically identical between winners and losers (June diagnosis, confirmed independently in the July re-analysis) | `volume_ratio` at entry found to separate winners/losers on the July window. Gate shipped `src/core/entry_volume_gate.py` + `[entry_volume_gate]` config, wired into `strategy_worker._execute_claude_trade` | ✅ Phase 0 (observe) shipped `5ea9823`; Phase 1 (enforce @ 0.30) code-complete, config flipped pending deploy — see note below |
| 20 | **June log bundle (source of the entries-quality diagnosis) rotated off the VM** — blocked any true cross-window validation of candidate entry filters | `scripts/daily_trade_export.py` extended to archive full `trade_log` + `trade_intelligence` tables to dated CSVs daily (`data/trade_logs/archive/`, 90-day retention), independent of log rotation | ✅ Fixed |
| 21 | **`trading-export.service` and `trading-healthcheck.service` silently failing on the VM since 2026-07-13/14** — both scripts lost their executable bit in the 2026-07-08 deploy (`-rw-rw-r--` instead of `-rwxrwxr-x`), systemd exec step failed with `Permission denied` every scheduled run, no alert surfaced it | `chmod +x` restored on both scripts on the VM | ✅ Fixed |

**Note on issue #19 enforcement:** Phase 1 (`mode="enforce"`) skips the
plan's original live-counterfactual gate (observe for ≥3 days / ≥200 trades
before enforcing) — the operator chose to enforce immediately on deploy
rather than wait for a live confirmation window. This is a deliberate
acceptance of one-window-validation risk, not an oversight; if the enforced
week's data disagrees with the July window, revert `config.toml
[entry_volume_gate] mode` to `"observe"` (instant, config-only).

## Open Issues Still to Address

1. **ESPORTSUSDT SL still too wide** — Even at 25% cap, DeepSeek's SL at 33.4% is rejected. The brain needs better ATR awareness or the prompt needs stronger SL guidance for extreme-vol coins.
2. **Position reconciler drift** — `db_count=0 live_count=1` streak persists in shadow mode. Minor but indicates the thesis manager and position reconciler are out of sync.
3. **Testnet orders still rejected for some coins** — SPCXUSDT, XLMUSDT orders rejected even after being added to shadow tracking.
4. **R:R fix (`d1b1561`) not yet measured live** — needs ~100 post-fix closed trades to confirm avg-win/avg-loss ratio moved from 0.64x toward the 1.5x target. Also verify the brain actually obeys "TP >= 2x SL" — the `apex_final_sl`/`apex_final_tp` columns were empty in the 2026-07-15 export; investigate why while measuring.
5. **Volume-ratio gate threshold tuning** — 0.30 was chosen conservatively (keeps ~57% of trades on the baseline window); 0.4-0.5 showed better net$ on the same window but that's threshold-mining until confirmed on enforced live data.
