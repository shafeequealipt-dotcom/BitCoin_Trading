"""AI Question Handler: routes free-form questions to Claude with context."""

from src.brain.claude_client import ClaudeClient
from src.brain.cost_tracker import CostTracker
from src.core.logging import get_logger
from src.telegram.ai.context_builder import ContextBuilder
from src.telegram.ai.prompts import TELEGRAM_AI_CONTEXT_PROMPT, TELEGRAM_AI_SYSTEM_PROMPT

log = get_logger("telegram")


class AIQuestionHandler:
    """Handles free-form AI questions via Claude.

    Args:
        context_builder: Builds rich context from DB.
        claude_client: Claude API client.
        cost_tracker: API budget tracker.
    """

    def __init__(
        self,
        context_builder: ContextBuilder,
        claude_client: ClaudeClient | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.context_builder = context_builder
        self.claude_client = claude_client
        self.cost_tracker = cost_tracker

    async def answer(self, question: str, symbol: str | None = None, conversation_context: str = "") -> str:
        """Answer a free-form question using Claude with market context.

        Returns the AI response text, or an error message.
        """
        if not self.claude_client:
            return "AI not available (Claude client not configured)"

        if self.cost_tracker and not self.cost_tracker.can_afford_call():
            return "Daily AI budget exhausted. Try again tomorrow."

        # Build context
        market_context = await self.context_builder.build(symbol)

        full_context = market_context
        if conversation_context:
            full_context = f"{conversation_context}\n\n{market_context}"

        prompt = TELEGRAM_AI_CONTEXT_PROMPT.format(
            context=full_context,
            question=question,
        )

        try:
            response = await self.claude_client.send_message(
                prompt=prompt,
                system_prompt=TELEGRAM_AI_SYSTEM_PROMPT,
            )
            # ClaudeClient returns dict {"text": ...}, ClaudeCodeClient returns str
            if isinstance(response, dict):
                return response.get("text", "")[:4000]
            return str(response)[:4000]
        except Exception as e:
            log.error("AI question failed: {err}", err=str(e))
            return f"AI response failed: {e}"
