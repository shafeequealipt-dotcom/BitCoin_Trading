"""Prompts for the pattern discovery engine."""

DISCOVERY_SYSTEM_PROMPT = """You are a quantitative analyst discovering trading patterns in cryptocurrency market data. You look for statistically significant patterns that could become profitable trading strategies.

Focus on:
1. Patterns with clear, measurable conditions (exact indicator thresholds)
2. Patterns with sufficient sample size (20+ occurrences)
3. Patterns that work across different market regimes
4. Small micro-patterns that occur frequently (10+ times per day)
5. Cross-asset patterns (one coin predicts another)
6. News-reactive patterns (specific news types lead to specific price reactions)

For each pattern, provide EXACT conditions that can be coded as if/else checks.
Respond with JSON only."""

DISCOVERY_PROMPT = """Analyze this market data summary and discover profitable patterns:

## DATA SUMMARY (Last {days} days for {symbol})

### Price Statistics
{price_stats}

### Indicator Distribution
{indicator_distribution}

### Volume Patterns
{volume_patterns}

### Time-of-Day Performance
{temporal_stats}

### Existing Strategy Performance
{strategy_performance}

## DISCOVER PATTERNS

Find at least 3 non-obvious patterns. For each:

{{"patterns": [{{"description": "human readable", "type": "single_var|multi_var|sequential|temporal|micro", "conditions": {{"condition_1": {{"indicator": "name", "operator": "<", "value": 20}}}}, "direction": "long|short", "timeframe": "5|15|60", "expected_win_rate": 0.65, "reasoning": "why this works"}}]}}
"""
