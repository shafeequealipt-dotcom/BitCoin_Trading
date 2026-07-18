"""OpenRouter API client wrapper with cost tracking and error handling.

Uses OpenAI SDK with OpenRouter base URL so the existing OPENAI_API_KEY
env-var works without an Anthropic API key.

Implements ClaudeCodeClient-interface parity: send_message returns str,
extract_json is available.
"""

import json
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
    can call LLM models without an Anthropic API key or the claude CLI.
    Interface parity with ClaudeCodeClient: send_message returns str,
    extract_json is available.
    """

    def __init__(
        self,
        settings: Settings,
        cost_tracker: CostTracker,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """OpenAI-compatible LLM client.

        Defaults to OpenRouter (base_url + OPENAI_API_KEY + settings.brain.model)
        for full back-compat. The keyword overrides let the same battle-tested
        client target any OpenAI-compatible provider — used by the "groq"
        provider branch in workers/manager.py (2026-07-18) to point at Groq
        without a parallel client class, since Groq speaks the identical API.
        The provider label is derived from the base_url for accurate logging.
        """
        self.settings = settings
        self.cost_tracker = cost_tracker
        resolved_key = (
            api_key
            if api_key is not None
            else (settings.brain.api_key or os.environ.get("OPENAI_API_KEY", ""))
        )
        if not resolved_key:
            log.warning("No API key set — Brain will not function")
            resolved_key = "dummy"
        resolved_base_url = base_url or OPENROUTER_BASE_URL
        self.client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
        )
        self.model = model if model is not None else settings.brain.model
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.brain.max_tokens
        )
        # Provider label for logs — "groq" vs "openrouter" — from the host.
        self.provider_label = (
            "groq" if "groq.com" in resolved_base_url else "openrouter"
        )
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self._last_call_time: float = 0.0
        self._last_response_time: float = 0.0
        self._consecutive_failures: int = 0
        self._current_call: str | None = None

    @retry(max_attempts=2, delay=5.0, exceptions=(ClaudeAPIError,))
    @timed
    async def send_message(self, prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        """Send a message via OpenRouter and return the raw response text.

        Interface parity with ClaudeCodeClient.send_message: returns str
        (the raw response text), accepts **kwargs for compatibility.

        Args:
            prompt: User message content.
            system_prompt: Optional system prompt.

        Returns:
            Raw response text string.

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
            self._current_call = prompt[:100]

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
            self._current_call = None

            choice = response.choices[0] if response.choices else None
            response_text = choice.message.content if choice else ""
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            cost = self.cost_tracker.record_call(input_tokens, output_tokens)

            self.total_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            log.info(
                "{prov} API call: {inp} in, {out} out, cost=${cost:.4f}",
                prov=self.provider_label, inp=input_tokens, out=output_tokens, cost=cost,
            )

            return response_text

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

    @staticmethod
    def extract_json(raw_response: str) -> dict | None:
        """Extract JSON from a raw response string.

        Interface parity with ClaudeCodeClient.extract_json. Strips
        markdown code fences and trailing commas, then parses JSON.
        """
        if not raw_response:
            return None
        text = raw_response.strip()
        if text.startswith("```"):
            end = text.find("\n", 3)
            if end != -1:
                text = text[end:].strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            return None

    async def analyze_market(self, market_state: str, system_prompt: str) -> str:
        """High-level method: analyze market state.

        Args:
            market_state: Formatted prompt with market data.
            system_prompt: System instructions.

        Returns:
            Raw response text.
        """
        return await self.send_message(market_state, system_prompt)

    def cancel_current_call(self) -> None:
        """Cancel any in-flight call — interface parity with ClaudeCodeClient."""
        self._current_call = None

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
