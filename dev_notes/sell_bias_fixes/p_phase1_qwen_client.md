# PRIMARY Issue — Phase 1 Step P.1.3: APEX Qwen Client + Prompts (CRITICAL)

Sources:
- `src/apex/qwen_client.py` (365 lines)
- `src/apex/prompts.py` (226 lines)
- `src/tias/repository.py` (excerpts — `get_situation_data` query)

Status: read end-to-end. Investigation only.

## 1. QwenClient — What It Sends and Receives

`QwenClient.optimize(...)` (qwen_client.py:134-296):

- HTTPS POST to `https://openrouter.ai/api/v1/chat/completions`.
- Headers: `Authorization: Bearer <api_key>`, `Content-Type: application/json`, `HTTP-Referer`, `X-Title`.
- Payload (lines 174-183):

```python
{
  "model": "deepseek/deepseek-v3.2",
  "messages": [
    {"role": "system",  "content": <APEX_SYSTEM_PROMPT>},
    {"role": "user",    "content": <build_apex_user_prompt(package)>},
  ],
  "response_format": {"type": "json_object"},
  "temperature": 0.2,
  "max_tokens": 800,
}
```

- Errors and retryability:
  - HTTP non-200 → `APEXOptimizationError(retryable=False)`
  - Non-JSON 200 body → retryable=True
  - Empty `choices` → retryable=True (Issue B fix 2026-05-08 root cause)
  - Empty `message.content` → retryable=True
  - Invalid content JSON (after fence strip) → retryable=True
  - Non-dict valid JSON → retryable=False
  - Timeout → retryable=False
  - aiohttp connection error → retryable=False
- The caller wraps in 1 bounded retry (`apex_max_attempts`).
- Successful return shape: `{content: dict, response_time_ms, input_tokens, output_tokens, cost_usd, model_used}`.

`APEX_QWEN_OK` (qwen_client.py:262, INFO) emitted on every success with model, latency, tokens, cost.

## 2. The System Prompt (`APEX_SYSTEM_PROMPT`, prompts.py:21-75)

Full text reproduced verbatim:

```
You are APEX — an aggressive trade optimizer for crypto futures.

A senior trader has already decided WHAT to trade. Your job is to decide HOW to trade it for MAXIMUM NET DOLLAR PROFIT per trade.

YOU DO NOT REJECT TRADES. YOU DO NOT SAY "SKIP."
Every directive you receive WILL be traded. Your job is to make it the most profitable version possible.

YOUR OPTIMIZATION TARGET: MAXIMUM NET DOLLAR PROFIT PER TRADE.
A trade that wins 60% of the time at $12 average win is BETTER than a trade that wins 90% at $3 average win. Optimize for total dollars captured, not win count.

KEY METRICS TO CONSIDER:
- Profit Factor (total $ won / total $ lost) — target above 2.0
- TP Fill Rate (% of intended TP actually captured) — target above 50%
- If TP Fill Rate in TIAS history is below 30%, your exit parameters are TOO TIGHT
- Avg Win $ vs Avg Loss $ — wins must be bigger than losses in dollars

YOUR MINDSET:
- PROFIT-MAXIMIZING: Larger wins matter more than higher win rate
- DATA-DRIVEN: The TIAS history is REAL trading data from this exact system. Trust it over theory.
- ADAPTIVE: If TIAS data overwhelmingly shows one direction outperforms the other IN THE CURRENT REGIME, consider flipping direction.
- REGIME-DIRECTION AWARENESS (CRITICAL):
  * trending_down regime: Sell is the NATURAL direction. Only flip Sell->Buy if Buy has >65% WR with >5 trades in THIS regime for THIS coin.
  * trending_up regime: Buy is the NATURAL direction. Only flip Buy->Sell if Sell has >65% WR with >5 trades in THIS regime for THIS coin.
  * ranging regime: Both directions valid. Use DIRECTION BREAKDOWN data to decide.
  * volatile regime: Be conservative. Only flip with overwhelming evidence (>70% WR, >8 trades).
- CONTAMINATION AWARENESS: If TIAS shows many LOSING trades in a counter-regime direction (e.g., Buy losses in trending_down), those losses were likely from prior bad flips. Do NOT use losing counter-regime trades as evidence to flip AGAIN.
- INSUFFICIENT DATA: If fewer than 5 trades exist for a direction in the current regime, that is NOT enough to justify a flip. Keep the trader's original direction.

WHAT YOU OPTIMIZE:
1. DIRECTION: Same as the trader OR flipped if TIAS overwhelmingly shows the opposite wins.
2. STOP LOSS: ATR-proportional...
[...]
```

### Observations

1. The prompt **encourages flipping** with explicit "ADAPTIVE" / "consider flipping direction" language (line 40).
2. The **trending regimes** have an asymmetric threshold (65% WR + 5 trades) before a flip is even considered. The ranging regime has **no numerical threshold** — it just says "use DIRECTION BREAKDOWN data". This is the loosest gate.
3. The CONTAMINATION AWARENESS clause is narrow — it only addresses "counter-regime" losses (e.g. Buy losses in trending_down). In ranging there is no "counter regime"; both directions are nominally valid, so the contamination warning does not apply.
4. "INSUFFICIENT DATA: <5 trades" is the only fallback into "keep the trader's original direction" for ranging — but a coin with even moderate trade history easily clears this floor.
5. The prompt does NOT contain any "default to brain" instruction for ranging. It hands DeepSeek the loosest mandate in the most-frequent regime.

## 3. The User Prompt Builder (`build_apex_user_prompt`, prompts.py:82-226)

Renders 4-5 sections plus output schema:

| Section | Content |
|---------|---------|
| 1 Trade-to-optimize | Symbol, trader's direction, SL, TP, leverage, size, signal score, strategy, reasoning |
| 2 Current coin data | `coin.format()` — TA indicators, Mode4, orderbook, volatility class, recTP%/recSL%/TP_CAP |
| 3 TIAS history for this coin | Win/loss record, avg PnL, EV per trade, profit factor, **direction breakdown (Buy WR / Sell WR / Buy net / Sell net)**, regime label, plus up to 5 past trades each with category + ds_why + ds_what_should_done + optimal SL/TP/size if available |
| 4 TIAS situation data | "all coins in similar conditions" — Buy WR%, Sell WR%, avg Buy PnL, avg Sell PnL, **direction_bias label**, common ds_category list |
| 5 X-RAY structural | `structural_data.format()` — support/resistance, structure (BOS), FVG, OB, sweeps, RR Long vs Short |
| Output | JSON schema (direction, sl_pct, tp_pct, tp_mode, position_size_usd, leverage, entry_timing, add_on_pullback, add_trigger_pct, add_size_pct, reasoning, confidence) |

### Section 3 direction-breakdown emission (per coin, regime-filtered)

```python
prompt += f"""  DIRECTION BREAKDOWN (regime={sit.regime}):
    Buy:  {len(_buy_trades)} trades, {_buy_wins}W ({_buy_wr:.0f}% WR), net ${_buy_pnl:+.2f}
    Sell: {len(_sell_trades)} trades, {_sell_wins}W ({_sell_wr:.0f}% WR), net ${_sell_pnl:+.2f}
"""
```

The trades fed here come from `hist.trades` — the per-coin history filtered to the current regime by `_gather_symbol_history(symbol, regime=regime_str)` in the assembler. So DeepSeek sees the coin-specific record in the current regime.

### Section 4 TIAS situation emission

```python
prompt += f"""TIAS SITUATION DATA (all coins in similar conditions):
  Conditions: {sit.regime} regime, F&G={sit.fear_greed}
  Trades in similar conditions: {sit.total_trades_in_condition}
  Buy win rate: {sit.buy_win_rate:.1f}% (avg PnL: {sit.avg_buy_pnl:+.2f}%)
  Sell win rate: {sit.sell_win_rate:.1f}% (avg PnL: {sit.avg_sell_pnl:+.2f}%)
  Direction bias: {sit.direction_bias}
"""
```

`sit.direction_bias` comes from `TIASRepo.get_situation_data` (src/tias/repository.py:440-518):
- Queries `trade_intelligence` filtered by `regime` and `fear_greed_value` within ±10 of current.
- Computes `buy_win_rate` and `sell_win_rate` for that regime+F&G window.
- `bias = "buy" if buy_wr > sell_wr + 10 else "sell" if sell_wr > buy_wr + 10 else "neutral"`.
- **Note: query does NOT filter by `exchange_mode`** — TIAS sees both bybit_demo AND shadow trades when building situation data.

## 4. CRITICAL FINDING — TIAS Situation Data Says BUY (and DeepSeek Ignores It)

A live SQL query against the same data TIAS would return:

```sql
SELECT direction, COUNT(*), AVG(CASE WHEN win=1 THEN 1.0 ELSE 0 END) AS wr
FROM trade_intelligence
WHERE regime='ranging'
GROUP BY direction;
```

Result (today, across both exchange modes — what TIAS Section 4 would feed to DeepSeek):

| direction | trades | win-rate |
|-----------|--------|----------|
| Buy       | 181    | **45.9%** |
| Sell      | 376    | **33.0%** |

Difference: 12.9 percentage points → **`direction_bias = "buy"`** in ranging.

DeepSeek thus receives Section 4 telling it: "In ranging regime, Buy WR is 45.9%, Sell WR is 33.0%, Direction bias: buy."

Yet today's log evidence shows DeepSeek flips Buy→Sell on 23/23 attempts that cleared the confidence gate (all in regime=ranging). **DeepSeek is flipping AGAINST the Section 4 evidence it was just given.**

Three candidate reasons (P.1.9 will disambiguate by inspecting actual prompts/responses):

1. **Section 3 per-coin breakdown overrides Section 4**: Section 3 is regime-filtered and coin-specific. For a coin where Sell happens to have a better short-window record (the system having traded it Sell-biased for days), the per-coin direction breakdown can disagree with the global one. DeepSeek may be over-weighting the proximate coin-specific data.

2. **F&G window narrows the situation data**: TIAS filters by `fear_greed_value BETWEEN fg-10 AND fg+10`. If the current F&G window catches a Sell-favoring subset (e.g. extreme fear pulls in different historical conditions), the global numbers can flip.

3. **The contamination feedback loop**: Section 3 includes `ds_what_should_done` text and `ds_category` for past trades. The most common ds_category for losing Sells in ranging is `REGIME_MISMATCH` (172 of all ranging Sells). Yet that very text is fed back to DeepSeek for future prompts — and DeepSeek may interpret "system did Sell and got REGIME_MISMATCH" as evidence to do Sell again (rather than as evidence to do Buy next time). The semantic asymmetry of the post-hoc verdict text is not well-explored.

## 5. The Output JSON Schema

```
{
  "direction": "Buy or Sell",
  "sl_pct": 0.0,
  "tp_pct": 0.0,
  "tp_mode": "fixed or trail_only or partial_trail",
  "position_size_usd": 0,
  "leverage": 0,
  "entry_timing": "immediate or wait_pullback",
  "add_on_pullback": false,
  "add_trigger_pct": 0.0,
  "add_size_pct": 0,
  "reasoning": "why these parameters",
  "confidence": 0.0
}
```

DeepSeek can flip direction by changing the `direction` field. The `confidence` field is what the gate checks. The `reasoning` field is captured but not parsed by code — it surfaces in `APEX_FLIP`'s reasoning string and in `trade_intelligence.apex_reasoning`.

## 6. Does The Prompt Explicitly Bias Direction? — Verdict

**No, not explicitly toward Sell or Buy.** The prompt is symmetric in its regime instructions (trending_down → Sell natural; trending_up → Buy natural; ranging → both valid). However:

- The prompt is permissive in ranging — only "use direction breakdown" guidance, no hard floor.
- The prompt invites flipping ("ADAPTIVE: consider flipping direction").
- Section 4 data does push toward Buy in ranging (correctly so per global stats) — yet DeepSeek ignores it.

The bias appears to be downstream of the prompt — either in DeepSeek's interpretation of the numbers, in the Section 3 per-coin data, or in the model's training-time priors about crypto in ranging conditions.

## 7. Hard Constraint Findings From System Prompt

Lines 69-73 declare constraints:
- Max position size: 1200 USD
- Max leverage: 5x
- SL: 0.2% to 5.0%
- TP: 0.3% to 8.0%

`gate.py` (Phase 1 P.1.2) enforces these as hard floors/ceilings.

## 8. Findings That Drive PRIMARY Phase 2

1. **Section 4 TIAS data says BUY in ranging — DeepSeek ignores it.** This is the central data-level mystery. P.1.9 must pull 10-20 actual prompts and check whether Section 3 systematically disagrees with Section 4 for each flipped trade.
2. **The prompt's "ADAPTIVE consider flipping" framing is asymmetric**: it teaches DeepSeek to look for flip opportunities. There is no equivalent "default to the trader unless overwhelming" floor in ranging.
3. **The contamination clause does not protect ranging**. It only addresses "counter-regime" losses. In ranging, both directions are "valid" so contamination logic is bypassed.
4. **The model name in config is `deepseek/deepseek-v3.2` (line 894-area of config.toml)** but OpenRouter returns the dated variant `deepseek-v3.2-20251201`. Any change to model would be a config change only.
5. **`response_format: {"type": "json_object"}` is on every call** since Issue B fix (2026-05-08). Empty-choices/empty-content failures have dropped per Issue B's analysis.
6. **Cost per call ≈ $0.001**. At today's rate of ~67 APEX_QWEN_OK in 9 h = ~$0.07/day. Negligible compared to misdirected trade PnL.

## 9. Pending For P.1.9

- Pull 10-20 actual `APEX_FLIP` events; reconstruct the prompt + DeepSeek response pair.
- Compare Section 3 per-coin direction breakdown to Section 4 situation data.
- Identify which sub-section's evidence DeepSeek is following.
- Verify the typo bug from P.1.2 (`structure_data` vs `structural_data`) by checking that all `APEX_FLIP_BLOCKED` lines show `rr_boost=0.00`.

## 10. Out-of-Scope Confirmation

- No code changes.
- Brain prompt construction unchanged.
- Bybit demo execution unchanged.
