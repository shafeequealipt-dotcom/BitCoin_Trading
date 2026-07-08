"""Trade decision prompt template for Claude Brain."""

TRADE_DECISION_PROMPT = """Analyze the current crypto market state and make ONE trading decision.

## CURRENT MARKET STATE

### Prices
{prices_section}

### Technical Analysis
{ta_section}

### News Summary (Last 6 Hours)
{news_section}

### Sentiment
{sentiment_section}

### Alternative Data
- Fear & Greed Index: {fear_greed_value} ({fear_greed_classification})
- Funding Rates: {funding_section}

### Current Open Positions
{positions_section}

### Account
- Total Equity: ${equity}
- Available Balance: ${available_balance}
- Unrealized PnL: ${unrealized_pnl}

### Risk Limits
- Max position size: {max_position_pct}% of equity
- Max leverage: {max_leverage}x
- Max open positions: {max_positions}
- Daily loss limit: {max_daily_loss_pct}%
- Stop-loss: MANDATORY

### Recent Performance
{performance_section}

## THESIS INVALIDATION (Mid-Hold Trade Management Fix Phase 3.2, 2026-05-19)

For each new trade, state the criterion under which the thesis is no longer valid. The watchdog monitors the criterion during hold and surfaces it back to you in the next prompt if it fires. Choose ONE of four types:
- "price_close_above": short justified by a structural ceiling. Value is the price level.
- "price_close_below": long justified by a structural floor. Value is the price level.
- "signal": trade justified by an ensemble/regime read. Value is one of: "ensemble_flip_to_strong_buy", "ensemble_flip_to_strong_sell", "regime_inverted", "mtf_alignment_broken".
- "none": no specific criterion applies. Value is null.

## RESPOND WITH EXACTLY THIS JSON STRUCTURE:
{{"action": "buy" | "sell" | "close" | "hold", "symbol": "BTCUSDT", "confidence": 0.0, "order_type": "market", "limit_price": null, "qty_pct": 0.0, "stop_loss": null, "take_profit": null, "leverage": 1, "thesis_invalidation": {{"type": "price_close_above|price_close_below|signal|none", "value": null}}, "reasoning": "", "risk_notes": ""}}"""
