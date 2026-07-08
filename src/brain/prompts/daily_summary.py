"""Daily summary prompt template for Claude Brain."""

DAILY_SUMMARY_PROMPT = """Generate a brief daily trading summary.

### Today's Activity
{activity_section}

### Performance
{performance_section}

### Market Overview
{market_overview}

Respond with JSON:
{{"summary": "2-3 sentence overview", "best_trade": null, "worst_trade": null, "lessons_learned": "", "tomorrow_outlook": "", "risk_recommendation": ""}}"""
