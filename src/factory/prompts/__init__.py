"""Factory prompt templates."""

from src.factory.prompts.discovery_prompt import DISCOVERY_PROMPT, DISCOVERY_SYSTEM_PROMPT
from src.factory.prompts.generation_prompt import GENERATION_PROMPT, GENERATION_SYSTEM_PROMPT

__all__ = [
    "DISCOVERY_SYSTEM_PROMPT", "DISCOVERY_PROMPT",
    "GENERATION_SYSTEM_PROMPT", "GENERATION_PROMPT",
]
