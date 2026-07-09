"""OpenRouter API client wrapper with cost tracking and error handling.

Uses OpenAI SDK with OpenRouter base URL so the existing OPENAI_API_KEY
env-var works without an Anthropic API key.
"""

import os
import time as _time

from openai import AsyncOpenAI
from openai import (
    RateLimitError as OpenAIRateLimitError,
    APIConnectionError as OpenAIConnectionError,
    APIError as OpenAIAPIError,
)

from src.config.settings import Settings
from src.core.decorators import retry, timed
from src.core.exceptions import BrainError, ClaudeAPIError
from src.core.logging import get_logger
from src.brain.cost_tracker import CostTracker

log = get_logger("brain")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ClaudeClient:
    """Wrapper around OpenRouter API (OpenAI-compatible) with cost tracking
    and budget enforcement.

    Uses the existing OPENAI_API_KEY and routes to OpenRouter so the brain
    can call Claude models (or any OpenRouter-supported model) without an
    Anthropic API key.
    """

    def __init__(self, settings: Settings, cost_tracker: CostTracker) -> None:
        self.settings = settings
        self.cost_tracker = cost_tracker
        api_key = settings.brain.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            log.warning("No API key set — Brain will not function")
            api_key = "dummy"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
        )
        self.model = settings.brain.model
        self.max_tokens = settings.brain.max_tokens
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self._last_call_time: float = 0.0
        self._last_response_time: float = 0.0
        self._consecutive_failures: int = 0

    @retry(max_attempts=2, delay=5.0, exceptions=(ClaudeAPIError,))
    @timed
    async def send_message(self, prompt: str, system_prompt: str | None = None) -> dict:
        """Send a message via OpenRouter and return the response with cost info.

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
            raise BrainError("Daily budget exceeded — cannot make API call")

        try:
            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            self._last_call_time = _time.time()

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://github.com/shafeequealipt-dotcom/BitCoin_Trading",
                    "X-Title": "Trading Bot Brain",
                },
            )

            self._last_response_time = _time.time()
            self._consecutive_failures = 0

            choice = response.choices[0] if response.choices else None
            response_text = choice.message.content if choice else ""
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            cost = self.cost_tracker.record_call(input_tokens, output_tokens)

            self.total_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            log.info(
                "OpenRouter API call: {inp} in, {out} out, cost=${cost:.4f}",
                inp=input_tokens, out=output_tokens, cost=cost,
            )

            return {
                "text": response_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "model": response.model if hasattr(response, "model") else self.model,
                "message_id": response.id if hasattr(response, "id") else "",
            }

        except OpenAIRateLimitError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"API rate limited: {e}", details={"error": str(e)})
        except OpenAIConnectionError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"API connection error: {e}", details={"error": str(e)})
        except OpenAIAPIError as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"API error: {e}", details={"error": str(e)})
        except BrainError:
            raise
        except Exception as e:
            self._consecutive_failures += 1
            raise ClaudeAPIError(f"Unexpected API error: {e}", details={"error": str(e)})

    async def analyze_market(self, market_state: str, system_prompt: str) -> dict:
        """High-level method: analyze market state.

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
