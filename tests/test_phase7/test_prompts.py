"""Tests for prompt templates."""

from src.brain.prompts import SYSTEM_PROMPT, TRADE_DECISION_PROMPT, RISK_REVIEW_PROMPT, DAILY_SUMMARY_PROMPT


class TestPromptTemplates:
    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 100
        assert "JSON" in SYSTEM_PROMPT
        assert "stop_loss" in SYSTEM_PROMPT
        assert "hold" in SYSTEM_PROMPT

    def test_trade_prompt_has_placeholders(self):
        assert "{prices_section}" in TRADE_DECISION_PROMPT
        assert "{ta_section}" in TRADE_DECISION_PROMPT
        assert "{news_section}" in TRADE_DECISION_PROMPT
        assert "{equity}" in TRADE_DECISION_PROMPT
        assert "{max_leverage}" in TRADE_DECISION_PROMPT

    def test_risk_prompt_has_placeholders(self):
        assert "{positions_section}" in RISK_REVIEW_PROMPT
        assert "{risk_status}" in RISK_REVIEW_PROMPT

    def test_daily_prompt_has_placeholders(self):
        assert "{activity_section}" in DAILY_SUMMARY_PROMPT
        assert "{performance_section}" in DAILY_SUMMARY_PROMPT

    def test_trade_prompt_formats_with_data(self):
        """Verify the template can be formatted without errors."""
        formatted = TRADE_DECISION_PROMPT.format(
            prices_section="BTC: $70000",
            ta_section="RSI: 42",
            news_section="5 articles, +0.3 sentiment",
            sentiment_section="Bullish",
            fear_greed_value=30,
            fear_greed_classification="Fear",
            funding_section="BTC: +0.03%",
            positions_section="None",
            equity=10000,
            available_balance=8000,
            unrealized_pnl=150,
            max_position_pct=10,
            max_leverage=3,
            max_positions=5,
            max_daily_loss_pct=5,
            performance_section="10 trades, 60% win rate",
        )
        assert "BTC: $70000" in formatted
        assert len(formatted) > 200
