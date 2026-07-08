"""Brain prompt templates."""

from src.brain.prompts.market_analysis import SYSTEM_PROMPT
from src.brain.prompts.trade_decision import TRADE_DECISION_PROMPT
from src.brain.prompts.risk_review import RISK_REVIEW_PROMPT
from src.brain.prompts.daily_summary import DAILY_SUMMARY_PROMPT
from src.brain.prompts.position_review import WATCHDOG_SYSTEM_PROMPT, POSITION_REVIEW_PROMPT
from src.brain.prompts.setup_review import SETUP_REVIEW_SYSTEM_PROMPT, SETUP_REVIEW_PROMPT
from src.brain.prompts.weekly_optimization import OPTIMIZATION_SYSTEM_PROMPT, OPTIMIZATION_REVIEW_PROMPT

__all__ = [
    "SYSTEM_PROMPT", "TRADE_DECISION_PROMPT", "RISK_REVIEW_PROMPT",
    "DAILY_SUMMARY_PROMPT", "WATCHDOG_SYSTEM_PROMPT", "POSITION_REVIEW_PROMPT",
    "SETUP_REVIEW_SYSTEM_PROMPT", "SETUP_REVIEW_PROMPT",
    "OPTIMIZATION_SYSTEM_PROMPT", "OPTIMIZATION_REVIEW_PROMPT",
]
