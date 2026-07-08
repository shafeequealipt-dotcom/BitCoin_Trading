"""TIAS Phase 2 — TradeAnalyzer: orchestrates DeepSeek post-trade analysis.

Responsibilities:
1. Build system + user prompts from a TradeIntelligence record.
2. Call DeepSeekClient with the primary model; fall back to fallback_model on
   retryable errors (HTTP 429, 503, timeout).
3. Map the DeepSeek JSON response to DB column names (ds_* fields).
4. Compute cost estimate from token counts.
5. Return a flat dict ready for TradeIntelligenceRepo.update_analysis().

This class NEVER writes to the DB — that is the caller's responsibility.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.config.settings import TIASSettings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.tias.categories import normalize_category
from src.tias.deepseek_client import DeepSeekClient, DeepSeekResponse, TIASAnalysisError
from src.tias.models import TradeIntelligence
from src.tias.prompts import TIAS_SYSTEM_PROMPT, build_user_prompt

log = get_logger("tias")

# DeepSeek V3 pricing via OpenRouter (USD per million tokens, as of 2025-01)
_COST_PER_M_INPUT = 0.27
_COST_PER_M_OUTPUT = 1.10


class TradeAnalyzer:
    """Orchestrates DeepSeek analysis for a single closed trade.

    Args:
        client: Initialized DeepSeekClient instance (shared across calls).
        settings: TIASSettings with model names, temperature, limits, version.
    """

    def __init__(self, client: DeepSeekClient, settings: TIASSettings) -> None:
        self._client = client
        self._settings = settings

    async def analyze(self, trade: TradeIntelligence) -> dict[str, Any]:
        """Run DeepSeek analysis on a closed trade and return DB-ready column dict.

        Attempts the primary model first. On a retryable TIASAnalysisError
        (rate-limit, service unavailable, timeout) tries the fallback model once.
        Non-retryable errors are re-raised immediately.

        Args:
            trade: Fully or partially populated TradeIntelligence from Phase 1 save.

        Returns:
            Dict with ds_* keys and metadata keys, ready for update_analysis().

        Raises:
            TIASAnalysisError: If both primary and fallback attempts fail.
        """
        user_prompt = build_user_prompt(trade)
        s = self._settings

        response = await self._call_with_fallback(
            system_prompt=TIAS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            primary_model=s.primary_model,
            fallback_model=s.fallback_model,
            temperature=s.temperature,
            max_tokens=s.max_tokens,
            timeout_seconds=s.timeout_seconds,
        )

        return self._map_response(response)

    async def _call_with_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        primary_model: str,
        fallback_model: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: int,
    ) -> DeepSeekResponse:
        """Attempt primary model; fall back to fallback_model on retryable error.

        Args:
            system_prompt: System-level analyst instructions.
            user_prompt: Formatted trade context.
            primary_model: First-choice OpenRouter model ID.
            fallback_model: Second-choice model (used on retryable failures only).
            temperature: Sampling temperature.
            max_tokens: Max completion tokens.
            timeout_seconds: Per-request timeout.

        Returns:
            DeepSeekResponse from whichever model succeeded.

        Raises:
            TIASAnalysisError: If the call fails (non-retryable) or both attempts fail.
        """
        try:
            return await self._client.analyze(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=primary_model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        except TIASAnalysisError as e:
            if not e.retryable:
                raise
            # Primary failed with retryable error — try fallback
            log.warning(
                "TIAS_FALLBACK | primary={pm} err='{err}' → trying fallback={fm} | {ctx}",
                pm=primary_model,
                err=str(e)[:150],
                fm=fallback_model,
                ctx=ctx(),
            )
            # Fallback attempt — let any exception (retryable or not) propagate
            return await self._client.analyze(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=fallback_model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )

    def _map_response(self, response: DeepSeekResponse) -> dict[str, Any]:
        """Map DeepSeek JSON response to ds_* DB column names + metadata.

        DeepSeek JSON key → DB column:
            why                      → ds_why
            category                 → ds_category
            correct_direction        → ds_correct_direction + ds_optimal_direction
            what_should_have_done    → ds_what_should_done
            how_to_exploit_next_time → ds_how_to_exploit
            optimal_sl_pct           → ds_optimal_sl_pct
            optimal_tp_pct           → ds_optimal_tp_pct
            optimal_size_usd         → ds_optimal_size_usd
            optimal_leverage         → ds_optimal_leverage
            confidence               → ds_confidence

        Metadata columns also populated:
            ds_analyzed_at, ds_raw_response, ds_model,
            ds_response_time_ms, ds_input_tokens, ds_output_tokens,
            ds_cost_usd, analysis_version

        Args:
            response: Validated DeepSeekResponse from the API call.

        Returns:
            Flat dict of DB-ready column → value pairs.
        """
        c = response.content

        # Safely extract typed fields from response
        def _str(key: str) -> str | None:
            val = c.get(key)
            return str(val).strip() if val is not None else None

        def _float(key: str) -> float | None:
            val = c.get(key)
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        def _int(key: str) -> int | None:
            val = c.get(key)
            try:
                return int(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        correct_direction = _str("correct_direction")

        # Issue #3 fix (2026-05-25): validate/normalize the model's category
        # against the single source-of-truth contract (src/tias/categories.py).
        # All current model output is in-set, so this is primarily a forward
        # guard plus observability: a value recognised after cleaning is logged
        # as normalized; an out-of-contract value is kept (never dropped) and
        # logged loudly so taxonomy drift is visible rather than silent.
        _raw_category = _str("category")
        _category, _cat_status = normalize_category(_raw_category)
        if _cat_status == "normalized":
            log.info(
                "TIAS_CATEGORY_NORMALIZED | raw='{r}' -> '{c}' | {ctx}",
                r=_raw_category, c=_category, ctx=ctx(),
            )
        elif _cat_status == "invalid":
            log.warning(
                "TIAS_CATEGORY_INVALID | raw='{r}' kept='{c}' not_in_contract | {ctx}",
                r=_raw_category, c=_category, ctx=ctx(),
            )

        # Cost estimation: DeepSeek V3 pricing via OpenRouter
        cost_usd = round(
            (response.input_tokens * _COST_PER_M_INPUT / 1_000_000)
            + (response.output_tokens * _COST_PER_M_OUTPUT / 1_000_000),
            8,
        )

        return {
            # Analysis content
            "ds_why": _str("why"),
            "ds_category": _category,
            "ds_correct_direction": correct_direction,
            "ds_optimal_direction": correct_direction,  # same field — direction that was/would be optimal
            "ds_what_should_done": _str("what_should_have_done"),
            "ds_how_to_exploit": _str("how_to_exploit_next_time"),
            "ds_optimal_sl_pct": _float("optimal_sl_pct"),
            "ds_optimal_tp_pct": _float("optimal_tp_pct"),
            "ds_optimal_size_usd": _float("optimal_size_usd"),
            "ds_optimal_leverage": _int("optimal_leverage"),
            "ds_confidence": _float("confidence"),
            # API response metadata
            "ds_analyzed_at": datetime.now(timezone.utc).isoformat(),
            "ds_raw_response": json.dumps(c, ensure_ascii=False)[:8000],  # guard very large responses
            "ds_model": response.model,
            "ds_response_time_ms": response.response_time_ms,
            "ds_input_tokens": response.input_tokens,
            "ds_output_tokens": response.output_tokens,
            "ds_cost_usd": cost_usd,
            "analysis_version": self._settings.analysis_version,
        }
