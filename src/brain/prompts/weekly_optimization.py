"""Prompt templates for weekly strategy optimization review."""

OPTIMIZATION_SYSTEM_PROMPT = """You are reviewing weekly trading strategy performance data.
Analyze the optimization report and approve or modify the proposed changes.

RULES:
- Respond with ONLY valid JSON
- Be conservative with changes -- small adjustments are better than large ones
- Strategies with < 20 trades don't have enough data for conclusions
- Consider market conditions when evaluating performance
"""

OPTIMIZATION_REVIEW_PROMPT = """Review this weekly strategy optimization report:

## PERIOD: {period_start} to {period_end}

## OVERALL PERFORMANCE
Total Trades: {total_trades}
Win Rate: {overall_win_rate:.1%}
Total PnL: {total_pnl:+.2f}%

## PROPOSED CHANGES
{changes_section}

## STRATEGY SUMMARY
{strategy_summary}

## RESPOND WITH JSON:
{{"approved_changes": [0, 1, 2], "rejected_changes": [], "additional_notes": ""}}
"""
