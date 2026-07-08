"""Strategy Generator: uses Claude API to write strategy code from discovered patterns."""

import json
import re

from src.brain.claude_client import ClaudeClient
from src.brain.cost_tracker import CostTracker
from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.factory.models.factory_types import DiscoveredPattern, GeneratedStrategy
from src.factory.prompts.generation_prompt import GENERATION_PROMPT, GENERATION_SYSTEM_PROMPT

log = get_logger("factory")


class StrategyGenerator:
    """Generates trading strategy Python code from discovered patterns using Claude.

    Args:
        settings: Application settings.
        claude_client: For Claude API calls.
        cost_tracker: For budget enforcement.
    """

    def __init__(
        self,
        settings: Settings,
        claude_client: ClaudeClient | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.settings = settings
        self.claude_client = claude_client
        self.cost_tracker = cost_tracker

    async def generate(self, pattern: DiscoveredPattern) -> GeneratedStrategy:
        """Generate a strategy from a discovered pattern.

        Calls Claude to write complete Python code. Retries up to max_retries
        on validation failure.
        """
        cfg = self.settings.factory
        strategy_name = f"GEN_{pattern.id.replace('pat_', '')}"
        class_name = "".join(w.capitalize() for w in strategy_name.split("_"))

        gen_strategy = GeneratedStrategy(
            id=generate_id("gen"),
            pattern_id=pattern.id,
            strategy_name=strategy_name,
            code="",
            generated_at=now_utc(),
        )

        if not self.claude_client or not self.cost_tracker:
            gen_strategy.validation_errors = ["Claude client not available"]
            return gen_strategy

        if not self.cost_tracker.can_afford_call():
            gen_strategy.validation_errors = ["Daily budget exceeded"]
            return gen_strategy

        prompt = GENERATION_PROMPT.format(
            pattern_description=pattern.description,
            win_rate=pattern.win_rate,
            occurrences=pattern.occurrences,
            timeframe=pattern.timeframe,
            direction=pattern.direction,
            conditions_json=json.dumps(pattern.conditions, indent=2),
            class_name=class_name,
            strategy_name=strategy_name,
        )

        for attempt in range(1, cfg.max_generation_retries + 1):
            try:
                response = await self.claude_client.send_message(
                    prompt=prompt,
                    system_prompt=GENERATION_SYSTEM_PROMPT,
                )
                # ClaudeClient returns dict {"text": ...}, ClaudeCodeClient returns str
                if isinstance(response, dict):
                    _resp_text = response.get("text", "")
                    _resp_model = response.get("model", "")
                    _resp_cost = response.get("cost_usd", 0)
                else:
                    _resp_text = str(response)
                    _resp_model = ""
                    _resp_cost = 0
                code = self._extract_code(_resp_text)
                gen_strategy.code = code
                gen_strategy.claude_model = _resp_model
                gen_strategy.generation_cost_usd += _resp_cost
                gen_strategy.generation_attempts = attempt

                if code:
                    gen_strategy.syntax_valid = self._check_syntax(code)
                    if gen_strategy.syntax_valid:
                        break

                # Retry with error feedback
                prompt = f"Previous code had errors. Fix and regenerate:\n{code}\n\nErrors: syntax invalid"

            except Exception as e:
                gen_strategy.validation_errors.append(f"Attempt {attempt}: {str(e)}")
                log.warning(
                    "Generation attempt {a} failed for {p}: {err}",
                    a=attempt, p=pattern.id, err=str(e),
                )

        log.info(
            "Generated strategy {name} from pattern {pid} (attempts={a}, cost=${c:.4f})",
            name=strategy_name, pid=pattern.id,
            a=gen_strategy.generation_attempts,
            c=gen_strategy.generation_cost_usd,
        )
        return gen_strategy

    async def generate_batch(
        self, patterns: list[DiscoveredPattern],
    ) -> list[GeneratedStrategy]:
        """Generate strategies for top patterns within cost limit."""
        cfg = self.settings.factory
        max_count = cfg.max_strategies_per_batch
        results: list[GeneratedStrategy] = []
        total_cost = 0.0

        for pattern in patterns[:max_count]:
            if total_cost >= cfg.generation_cost_limit_usd:
                log.info("Generation cost limit reached (${c:.4f})", c=total_cost)
                break

            strategy = await self.generate(pattern)
            results.append(strategy)
            total_cost += strategy.generation_cost_usd

        return results

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extract Python code from Claude's response."""
        # Try markdown code fence
        match = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # If no fence, assume entire response is code
        if "class " in text and "def " in text:
            return text.strip()
        return ""

    @staticmethod
    def _check_syntax(code: str) -> bool:
        """Quick syntax check via compile()."""
        try:
            compile(code, "<generated>", "exec")
            return True
        except SyntaxError:
            return False
