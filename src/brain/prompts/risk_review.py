"""Risk review prompt template for Claude Brain."""

RISK_REVIEW_PROMPT = """Review current open positions and assess if any should be closed or adjusted.

### Open Positions
{positions_section}

### Current Market Conditions
{market_conditions}

### Risk Status
{risk_status}

For each position, recommend: "keep", "tighten_stop", "take_partial_profit", or "close".

Respond with JSON:
{{"reviews": [{{"symbol": "BTCUSDT", "action": "keep", "new_stop_loss": null, "reasoning": "why"}}], "overall_risk_assessment": "low"}}"""
