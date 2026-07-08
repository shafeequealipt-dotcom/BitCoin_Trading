"""Anthropic Claude API client wrapper with cost tracking and error handling."""

import time as _time

import anthropic

from src.config.settings import Settings
from src.core.decorators import retry, timed
from src.core.exceptions import BrainError, ClaudeAPIError
from src.core.logging import get_logger
from src.brain.cost_tracker import CostTracker

log = get_logger("brain")


class ClaudeClient:
    """Wrapper around the Anthropic SDK with cost tracking and budget enforcement.

    Args:
        settings: Application settings with brain.api_key and brain.model.
        cost_tracker: CostTracker for budget enforcement.
    """

    def __init__(self, settings: Settings, cost_tracker: CostTracker) -> None:
        self.settings = settings
        self.cost_tracker = cost_tracker
        api_key = settings.brain.api_key
        if not api_key:
            log.warning("Anthropic API key not set — Brain will not function")
        self.client = anthropic.AsyncAnthropic(api_key=api_key or "dummy")
        self.model = settings.brain.model
        self.max_tokens = settings.brain.max_tokens
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        # Phase 9a: watchdog heartbeat metrics. Watchdog consults these to
        # decide whether Claude is alive. Both _last_call_time and
        # _last_response_time are wall-clock timestamps:
        #   _last_call_time       = start of the most recent API call
        #   _last_response_time   = receipt of the most recent successful response
        # Heartbeat uses max() of both so a long in-flight call counts as alive.
        self._last_call_time: float = 0.0
        self._last_response_time: float = 0.0
        self._consecutive_failures: int = 0

    @retry(max_attempts=2, delay=5.0, exceptions=(ClaudeAPIError,))
    @timed
    async def send_message(self, prompt: str, system_prompt: str | None = None) -> dict:
        """Send a message to Claude and return the response with cost info.

        Args:
            prompt: User message content.
            system_prompt: Optional system prompt.

        Returns:
            Dict with text, tokens, cost, model, message_id.

        Raises:
            BrainError: If daily budget exceeded.
            ClaudeAPIError: On API errors.
        """
        if not self.cost_tracker.can_afford_call():
            raise BrainError("Daily budget exceeded — cannot make Claude API call")

        try:
            kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            # Heartbeat: record the CALL start before awaiting — so watchdog
            # sees the client as alive during a long in-flight request.
            self._last_call_time = _time.time()

            response = await self.client.messages.create(**kwargs)

            # Heartbeat: record the RESPONSE receipt on success; reset
            # consecutive-failure counter.
            self._last_response_time = _time.time()
            self._consecutive_failures = 0

            response_text = response.content[0].text if response.content else ""
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            cost = self.cost_tracker.record_call(input_tokens, output_tokens)

            self.total_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            log.info(
                "Claude API call: {inp} in, {out} out, cost=${cost:.4f}",
                inp=input_tokens, out=output_tokens, cost=cost,
            )

            return {
                "text": response_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "model": response.model,
                "message_id": response.id,
            }

        except anthropic.RateLimitError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Claude rate limited: {e}", details={"error": str(e)})
        except anthropic.APIConnectionError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Claude connection error: {e}", details={"error": str(e)})
        except anthropic.APIError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Claude API error: {e}", details={"error": str(e)})
        except BrainError:
            raise
        except Exception as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Unexpected Claude error: {e}", details={"error": str(e)})

    async def analyze_market(self, market_state: str, system_prompt: str) -> dict:
        """High-level method: analyze market state with Claude.

        Args:
            market_state: Formatted prompt with market data.
            system_prompt: System instructions.

        Returns:
            Full response dict from send_message.
        """
        return await self.send_message(market_state, system_prompt)

    def get_usage_stats(self) -> dict:
        """Get lifetime usage statistics."""
        total_cost = self.cost_tracker.lifetime_cost
        avg_cost = total_cost / self.total_calls if self.total_calls > 0 else 0
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_call": round(avg_cost, 6),
        }
